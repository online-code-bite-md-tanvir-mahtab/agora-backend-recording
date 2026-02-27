import datetime
import json
import os
import base64
import token
import requests
from flask import Flask, Response, request, jsonify
from google.cloud import storage
from google.oauth2 import service_account
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.twiml.voice_response import VoiceResponse, Dial, Say
from twilio.rest import Client
from agora_token_builder import RtcTokenBuilder

import firebase_admin
from firebase_admin import credentials as firebase_credentials, messaging, firestore


# ================= CONFIG =================



APP_ID = os.environ.get("AGORA_APP_ID")
CUSTOMER_ID = os.environ.get("AGORA_CUSTOMER_ID")
CUSTOMER_SECRET = os.environ.get("AGORA_CUSTOMER_SECRET")
AGORA_ACCESS_KEY = os.environ.get("AGORA_GCS_ACCESS_KEY")
AGORA_SECRET_KEY = os.environ.get("AGORA_GCS_SECRET_KEY")

APP_CERTIFICATE = os.environ.get("AGORA_APP_CERTIFICATE")
BUCKET_NAME = os.environ.get("AGORA_BUCKET_NAME")
SIG_SIP_URI = os.environ.get("SIGNALWIRE_SIP_URI")
SIG_USERNAME = os.environ.get("SIGNALWIRE_USERNAME")
SIG_PASSWORD = os.environ.get("SIGNALWIRE_PASSWORD")
TOKEN = os.environ.get("AGORA_TOKEN")  # Optional: only needed if your channel requires a token for recording

account_sid = os.getenv('TWILIO_ACCOUNT_SID')
auth_token = os.getenv('TWILIO_AUTH_TOKEN') 
twiml_app_sid = os.getenv('TWIML_APP_SID')
api_key_sid = os.getenv('TWILIO_API_KEY_SID') 
api_key_secret = os.getenv('TWILIO_API_KEY_SECRET') 
your_twilio_number = os.getenv('TWILIO_PHONE_NUMBER')   # +15078703438

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])

credentials = service_account.Credentials.from_service_account_info(
    service_account_info
)

storage_client = storage.Client(credentials=credentials)

# Initialize Firebase Admin ONCE at startup
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
service_account_info = json.loads(service_account_json)

# 🔥 Fix newline formatting
service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")

firebase_cred = firebase_credentials.Certificate(service_account_info)

# firebase_cred = firebase_credentials.Certificate(
#     json.loads(service_account_json)
# )

firebase_admin.initialize_app(firebase_cred)
db = firestore.client()  # or your preferred database client

# =========================================

app = Flask(__name__)


# =========================================
# Helper → Agora Auth Header
# =========================================
def agora_auth():
    credentials = f"{CUSTOMER_ID}:{CUSTOMER_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json"
    }


# =========================================
# Acquire resource
# =========================================

@app.route("/")
def home():
    return "Agora Cloud Recording API is running!"


@app.route("/acquire", methods=["POST"])
def acquire():
    data = request.json
    channel = data["channel"]
    uid = str(data["uid"])

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/acquire"

    payload = {
        "cname": channel,
        "uid": uid,
        "clientRequest": {}
    }

    response = requests.post(
        url,
        auth=(CUSTOMER_ID, CUSTOMER_SECRET),
        json=payload
    )

    return jsonify(response.json())


# =========================================
# Start recording
# =========================================
@app.route("/start", methods=["POST"])
def start():
    channel = request.json["channel"]
    uid = request.json.get("uid", "0")
    resource_id = request.json["resourceId"]

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/resourceid/{resource_id}/mode/mix/start"

    payload = {
        "cname": channel,
        "uid": uid,
        "clientRequest": {
            "token": TOKEN,  # Add RTC token here if your channel requires it
            "recordingConfig": {
                "maxIdleTime": 300,           # 5 minutes idle timeout
                "streamTypes": 3,             # 3 = audio only (recommended for calls; use 2 if you want video too)
                "channelType": 0,             # 0 = communication mode
                "audioProfile": 0,            # Default audio quality
                "audioCodecProfile": 0,
                "postponeTranscoding": True   # Helps with MP4 generation
            },
            "recordingFileConfig": {
                "avFileType": ["hls", "mp4"]  # Required for MP4 output in mix mode
            },
            "storageConfig": {
                "vendor": 6,                  # 2 = Google Cloud Storage
                "region": 0,                  # Adjust if your bucket is in a specific region (check Agora docs)
                "bucket": BUCKET_NAME,
                "accessKey": AGORA_ACCESS_KEY,
                "secretKey": AGORA_SECRET_KEY,
                "fileNamePrefix": ["records"] # Prefix for files in bucket
            }
        }
    }

    r = requests.post(url, headers=agora_auth(), json=payload)
    return jsonify(r.json())


# =========================================
# Stop recording
# =========================================
@app.route("/stop", methods=["POST"])
def stop():
    channel = request.json["channel"]
    uid = request.json.get("uid", "0")
    resource_id = request.json["resourceId"]
    sid = request.json["sid"]
    print("Stopping recording for resource:", resource_id, "sid:", sid, "channel:", channel, "uid:", uid)

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/resourceid/{resource_id}/sid/{sid}/mode/mix/stop"

    payload = {
        "cname": channel,
        "uid": uid,
        "clientRequest": {}
    }

    r = requests.post(url, headers=agora_auth(), json=payload)
    print("Stop recording response:", r.status_code, r.text)
    return jsonify(r.json())

# =========================================
# Query recording status
# =========================================
@app.route("/query", methods=["POST"])
def query_recording():
    data = request.json

    resource_id = data["resourceId"]
    sid = data["sid"]

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/resourceid/{resource_id}/sid/{sid}/mode/mix/query"

    r = requests.get(url, headers=agora_auth())

    return jsonify(r.json())


# =========================================
# Webhook from Agora
# =========================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Webhook received:", data)

    try:
        service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info
        )

        storage_client = storage.Client(credentials=credentials)
        bucket = storage_client.bucket("your-bucket-name")

        file_list = data.get("payload", {}).get("fileList", [])
        download_links = []

        for file_info in file_list:
            file_name = file_info.get("fileName")
            blob = bucket.blob(file_name)

            url = blob.generate_signed_url(
                version="v4",
                expiration=datetime.timedelta(hours=1),
                method="GET",
            )

            download_links.append({
                "file_name": file_name,
                "download_url": url
            })

        return jsonify({"files": download_links})

    except Exception as e:
        return jsonify({"error": str(e)}), 500




# ==============================
# Route → make phone call
# ==============================
@app.route("/make-call", methods=["POST"])
def make_call():
    data = request.json

    channel = data.get("channel")
    phone_number = data.get("phone")
    token = data.get("token")  # token for SIP bot uid
    uid = data.get("uid", 0)

    if not channel or not phone_number or not token:
        return jsonify({"error": "missing data"}), 400

    url = f"https://api.agora.io/v1/projects/{APP_ID}/sip-gateway/nodes"

    payload = {
        "rtcConfig": {
            "channelName": channel,
            "uid": uid,
            "token": token
        },
        "sipConfig": {
            "uri": SIG_SIP_URI,
            "username": SIG_USERNAME,
            "password": SIG_PASSWORD,
            "callee": phone_number
        }
    }

    response = requests.post(
        url,
        headers=agora_auth(),
        json=payload
    )

    return jsonify(response.json()), response.status_code

# ===============
# Twilio test route
# ===============

client = Client(account_sid, auth_token)

@app.route('/token', methods=['POST'])
def generate_token():
    try:
        print("Received token generation request with data:", request.get_json())
        data = request.get_json()
        channel_name = data["channel"]
        uid = data["uid"]  # 0 = bot/host, or pass real user ID
        role = data["role"] # or Role_Publisher
        print(f"Generating token for channel: {channel_name}, uid: {uid}, role: {role}")

        # Token expiration (recommended: 24 hours = 86400 seconds)
        expiration_in_seconds = 86400
        current_timestamp = int(datetime.time.time())
        privilege_expired_ts = current_timestamp + expiration_in_seconds
        print(f"Current timestamp: {current_timestamp}, token will expire at: {privilege_expired_ts}")

        # Generate token
        token = RtcTokenBuilder.build_token_with_uid(
            APP_ID,
            APP_CERTIFICATE,
            channel_name,
            uid,
            role,
            privilege_expired_ts
        )
        print("Generated Agora token:", token)

        return jsonify({
            "success": True,
            "token": token,
            "channel": channel_name,
            "uid": uid,
            "expires_in": expiration_in_seconds
        })

    except Exception as e:
        print("Error generating token:", e)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

from twilio.twiml.voice_response import VoiceResponse, Dial, Say
# ... other imports ...

@app.route('/voice', methods=['POST'])
def voice():
    resp = VoiceResponse()

    # Optional: Custom welcome (Agora will still prompt for PIN after this)
    resp.say("Welcome to the audio session. Please enter your PIN when prompted.", voice="Polly.Joanna")

    dial = Dial(callerId="+15078703438")  # your existing number

    # Use the regional SIP URI Agora provided – pick closest to your users
    sip_uri = "sip:agora736.pstn.ashburn.twilio.com"  # Virginia (US East) – change as needed

    # Optional: Add parameters if Agora requires (rare)
    # sip_uri += ";transport=tcp"

    dial.sip(sip_uri)
    dial.timeout = 60  # Give time for PIN entry

    resp.append(dial)

    return Response(str(resp), mimetype='text/xml')


@app.route('/webhook/call-events', methods=['POST'])
def pstn_webhook():
    data = request.json
    print("PSTN Webhook received:", data)
    # Log to file/console or send to your Flutter app via push/FCM
    if data.get('event') == 'agora_bridge_start':
        print("AUDIO BRIDGE SUCCESSFULLY STARTED!")
    elif data.get('event') == 'agora_bridge_end':
        print("Bridge ended")
    return '', 204

# Optional: Status callback if you want to trigger Agora recording start/stop
@app.route('/call-status', methods=['POST'])
def call_status():
    call_status = request.values.get('CallStatus')
    call_sid = request.values.get('CallSid')

    if call_status in ['in-progress', 'ringing']:
        # Trigger Agora cloud recording start here (your existing code)
        print(f"Call {call_sid} in progress → start Agora recording")
        # your_agora_start_recording_logic(call_sid)

    elif call_status in ['completed', 'no-answer', 'busy']:
        # Trigger Agora stop/delete
        print(f"Call {call_sid} ended → stop Agora recording")
        # your_agora_stop_recording_logic(call_sid)

    return '', 204


@app.route('/generate-inbound', methods=['POST'])
def generate_inbound():
    data = request.json
    channel = data.get('channel', 'test_channel')

    # Call Agora API
    resp = requests.post(
        'https://sipcm.agora.io/v1/api/pstn',
        headers={
            'Authorization': 'Basic kV7mZp3xBw1QrT9nYj6Lf2HcUo8EgS4dAiX5tR',
            'Content-Type': 'application/json'
        },
        json={
  "action":"inboundsip",
  "appid":APP_ID,
  "token":TOKEN,
  "uid":"0",
  "channel":channel,
  "region":"AREA_CODE_NA"
}
    )
    print("Agora API response:", resp.status_code, resp.text)
    if resp.status_code == 200:
        return jsonify(resp.json()), 200
    else:
        return jsonify({"error": resp.text}), 500
    
@app.route("/inbound", methods=["POST"])
def inbound_call():
    from_number = request.values.get("From")
    call_sid = request.values.get("CallSid")

    print(f"Incoming call from {from_number} - SID: {call_sid}")

    # 1. Generate the SIP URI for this session
    resp = requests.post(
        'https://sipcm.agora.io/v1/api/pstn',
        headers={
            'Authorization': 'Basic kV7mZp3xBw1QrT9nYj6Lf2HcUo8EgS4dAiX5tR',
            'Content-Type': 'application/json'
        },
        json={
            "action": "inboundsip",
            "appid": APP_ID,
            "token": TOKEN,
            "uid": "0",
            "channel": "test_channel",  # or dynamic
            "region": "AREA_CODE_NA"
        }
    )

    if resp.status_code != 200:
        print("Failed to get SIP URI:", resp.text)
        # Fallback TwiML
        vr = VoiceResponse()
        vr.say("Sorry, we couldn't connect you right now.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    sip_data = resp.json()
    sip_uri = sip_data.get("sip")

    if not sip_uri:
        print("No SIP URI returned")
        vr = VoiceResponse()
        vr.say("Sorry, connection failed.")
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    print("Using SIP URI:", sip_uri)

    # for fcm push notifications to Flutter app, you can send the call_sid or other identifiers here so your app can correlate and display incoming call UI
# 1. Find FCM token for the user who owns this Twilio number
    # Replace with your real DB lookup
    user_fcm_token = "cc0383ioQEC6uYfbmxzh1w:APA91bF5rGvIzJJAEE6sWSitadcFNDNZ85XQe_xW4eu4RANqmGANoX_pIl-pWPwaDoJMCXM5hZ1e1qigzjWnw_2txOy1ANtW3f8MIlkKHSa-F1ceL5Ohl-k" # ← get from your database

    if user_fcm_token:
        message = messaging.Message(
            notification=messaging.Notification(
                title="Incoming Call",
                body=f"Call from {from_number}",
            ),
            data={
                'call_sid': call_sid,
                'caller': from_number,
                'type': 'incoming_call',
                'channel': 'test_channel'  # or dynamic
            },
            token=user_fcm_token,
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    sound='default',
                    channel_id='call_notifications'  # create high-priority channel in app
                )
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(title="Incoming Call", body=f"Call from {from_number}"),
                        sound='default',
                        badge=1,
                        category='CALL',
                    )
                )
            ),
        )

        try:
            response = messaging.send(message)
            print("FCM push sent successfully:", response)
        except Exception as e:
            print("FCM push failed:", e)
    # 2. Return TwiML to bridge to the returned SIP URI
    vr = VoiceResponse()
    vr.say("Connecting you now. Please hold.", voice="Polly.Joanna")

    dial = Dial(callerId="+15078703438")  # optional - your caller ID
    dial.sip(sip_uri)
    dial.timeout = 60

    vr.append(dial)

    vr.say("The session has ended. Goodbye.")

    return Response(str(vr), mimetype="text/xml")


@app.route('/twilio/call-lookup', methods=['POST'])
def call_lookup():
    # Optional: read To/From/CallSid if you want dynamic logic later
    # to_number = request.values.get('To')
    # from_number = request.values.get('From')

    # For now: fixed channel + generate token if needed
    token = get_access_token("test_channel", 0)  # your token function

    return jsonify({
        "channel": "test_channel",
        "uid": 0,
        "token": TOKEN
    })


@app.route('/save-fcm-token', methods=['POST'])
def save_fcm_token():
    data = request.json
    user_id = data.get('user_id')
    fcm_token = data.get('fcm_token')
    device_type = data.get('device_type')

    # Save to your database (e.g. Firebase Firestore, MongoDB, PostgreSQL)
    # Example pseudo-code
    db.users.update_one(
        {'user_id': user_id},
        {'$set': {'fcm_token': fcm_token, 'device_type': device_type}},
        upsert=True
    )

    print(f"Saved FCM token for user {user_id}: {fcm_token}")
    return jsonify({"status": "success"}), 200

# =========================================

# if __name__ == "__main__":
#     app.run(port=5000, debug=True)
