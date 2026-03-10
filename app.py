
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
from google.cloud.firestore_v1 import FieldFilter

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

@app.route('/tokens', methods=['POST'])
def get_access_token():
    identity = request.json.get('identity')  # e.g. "user_123" — must match what you register in Flutter
    if not identity:
        return jsonify({"error": "identity required"}), 400

    token = AccessToken(
        account_sid,
        api_key_sid,
        api_key_secret,
        identity=identity
    )

    voice_grant = VoiceGrant(
        outgoing_application_sid=twiml_app_sid,
        incoming_allow=True
        # push_credential_sid=...  ← add later if you want push for incoming
    )
    token.add_grant(voice_grant)

    return jsonify({"token": token.to_jwt().decode()})

@app.route('/save-fcm-token', methods=['POST'])
def save_fcm_token():
    try:
        data = request.get_json()
        if not data or 'token' not in data:
            return jsonify({"success": False, "error": "Missing 'token'"}), 400

        token = data['token']
        user_id = data.get('userId')
        phone_number = data.get('phoneNumber')  # optional

        if not user_id:
            return jsonify({"success": False, "error": "userId required"}), 400

        # Save in Firestore
        doc_ref = db.collection('users').document(user_id)
        doc_ref.set({
            'fcmToken': token,
            'phoneNumber': phone_number,
            'lastUpdated': firestore.SERVER_TIMESTAMP,
            'deviceInfo': data.get('deviceInfo'),
        }, merge=True)

        print(f"FCM token saved for user {user_id}: {token[:10]}...")

        return jsonify({"success": True, "message": "Token saved"})

    except Exception as e:
        print(f"Save FCM error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

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
    token = request.json.get("agora_token", TOKEN)  # Optional: pass token if your channel requires it

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/resourceid/{resource_id}/mode/mix/start"

    payload = {
        "cname": channel,
        "uid": uid,
        "clientRequest": {
            "token": token,  # Add RTC token here if your channel requires it
            "recordingConfig": {
                "maxIdleTime": 300,           # 5 minutes idle timeout
                "streamTypes": 3,             # 3 = audio only (recommended for calls; use 2 if you want video too)
                "channelType": 0,             # 0 = communication mode
                "audioProfile": 0,            # Default audio quality
                "audioCodecProfile": 0,
                "postponeTranscoding": True,   # Helps with MP4 generation
                "enableAudioAnnouncement": True,
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
        current_timestamp = int(datetime.datetime.now().timestamp())
        privilege_expired_ts = current_timestamp + expiration_in_seconds
        print(f"Current timestamp: {current_timestamp}, token will expire at: {privilege_expired_ts}")

        # Generate token
        # Error generating token: type object 'RtcTokenBuilder' has no attribute 'build_token_with_uid'
        token = RtcTokenBuilder.buildTokenWithUid(
            APP_ID,
            APP_CERTIFICATE,
            "test_channel",
            0,
            1,
            int(datetime.datetime.now().timestamp()) + 86400
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
    elif data.get('event') == 'call_hangup':
        print("Call was hung up by the user")
    elif data.get('event') == 'agora_bridge_failed':
        print("Bridge failed to start")
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
    
    


@app.route('/get-agora-token', methods=['POST'])
def get_agora_token():
    try:
        data = request.get_json()
        user_id = data.get('userId')
        phone = data.get('phoneNumber')
        channel = data.get('channel')

        if not user_id and not phone:
            return jsonify({"success": False, "error": "userId or phoneNumber required"}), 400

        # Prefer userId > phone > channel
        doc_id = user_id or phone or f"{channel}_0"
        doc_ref = db.collection('agora_tokens').document(doc_id)
        doc = doc_ref.get()

        if doc.exists:
            token_data = doc.to_dict()
            return jsonify({
                "success": True,
                "token": token_data.get('rtcToken'),
                "channel": token_data.get('channel'),
                "uid": token_data.get('uid'),
                "expiresAt": token_data.get('expiresAt')
            })
        else:
            return jsonify({"success": False, "error": "No token found"}), 404

    except Exception as e:
        print(f"Retrieve error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    


# Global in-memory storage for active calls (Vercel single-instance safe for now)
active_calls = {}  # key: call_sid, value: {'from': ..., 'channel': ..., 'status': 'active'}

@app.route("/inbound", methods=["POST"])
def inbound_call():
    from_number = request.values.get("From")
    call_sid = request.values.get("CallSid")

    print(f"Incoming call from {from_number} - SID: {call_sid}")

    # === NEW: Store call SID when call arrives ===
    active_calls[call_sid] = {
        'from': from_number,
        'channel': "test_channel",
        'status': 'active',
        'timestamp': datetime.datetime.now().isoformat()
    }
    print(f"Stored active call SID: {call_sid}")

    token  = RtcTokenBuilder.buildTokenWithUid(
            APP_ID,
            APP_CERTIFICATE,
            "test_channel",
            0,
            1,
            int(datetime.datetime.now().timestamp()) + 86400
        )
    # New collection name: 'agora_tokens'
    # Document ID: user_id or phone or auto-generated
    doc_id = from_number if from_number else f"unknown_0_{int(datetime.datetime.now().timestamp())}"

    doc_ref = db.collection('agora_tokens').document(doc_id)

    doc_ref.set({
        'rtcToken': token,
        'channel': "test_channel",
        'uid': "0",
        'phoneNumber': from_number,
        'createdAt': firestore.SERVER_TIMESTAMP,
        'updatedAt': firestore.SERVER_TIMESTAMP,
    }, merge=True)

    print(f"Token saved in 'agora_tokens/{doc_id}'")

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
            "token": token,
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
    
    # Fetch FCM token once
    users_ref = db.collection('users')
    query = users_ref.where(filter=FieldFilter('phoneNumber', '==', "+15078703438")).limit(1)
    docs = query.get()

    # for fcm push notifications to Flutter app, you can send the call_sid or other identifiers here so your app can correlate and display incoming call UI
# 1. Find FCM token for the user who owns this Twilio number
    # Replace with your real DB lookup
    user_fcm_token = None
    user_id = None

    if docs:
        user_doc = docs[0]
        user_data = user_doc.to_dict()
        user_fcm_token = user_data.get('fcmToken')
        user_id = user_doc.id  # the document ID (user123)

        if user_fcm_token:
            print(f"FCM token found for user {user_id} (phone {from_number}): {user_fcm_token[:10]}...")
        else:
            print(f"User {user_id} has no fcmToken field")
    else:
        print(f"No user found with phoneNumber {from_number}")
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


@app.route('/end-call', methods=['POST'])
def end_call():
    try:
        data = request.get_json()
        call_sid = data.get('call_sid')

        if not call_sid:
            return jsonify({"success": False, "error": "call_sid required"}), 400

        doc_ref = db.collection('active_calls').document(call_sid)

        # Check if document exists before update
        doc = doc_ref.get()

        if not doc.exists:
            print(f"No active call document found for SID {call_sid}")
            # Still attempt Twilio hangup (in case it's active)
            try:
                client.calls(call_sid).update(status='completed')
                print(f"Twilio call {call_sid} force-ended even without Firestore doc")
            except Exception as twilio_err:
                print(f"Twilio hangup failed: {twilio_err}")
            return jsonify({"success": True, "message": "Call ended (no Firestore doc)"}), 200

        # Document exists → safe to update
        doc_ref.update({'status': 'ended', 'ended_at': firestore.SERVER_TIMESTAMP})

        # Force hangup via Twilio
        try:
            client.calls(call_sid).update(status='completed')
            print(f"Call {call_sid} ended successfully")
        except Exception as e:
            print(f"Twilio hangup failed: {e}")

        return jsonify({"success": True, "message": "Call ended"}), 200

    except Exception as e:
        print(f"End call error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# =========================================

# if __name__ == "__main__":
#     app.run(port=5000, debug=True)