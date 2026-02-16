import datetime
import json
import os
import base64
import requests
from flask import Flask, request, jsonify
from google.cloud import storage
from google.oauth2 import service_account


# ================= CONFIG =================



APP_ID = os.environ.get("AGORA_APP_ID")
CUSTOMER_ID = os.environ.get("AGORA_CUSTOMER_ID")
CUSTOMER_SECRET = os.environ.get("AGORA_CUSTOMER_SECRET")
AGORA_ACCESS_KEY = os.environ.get("AGORA_GCS_ACCESS_KEY")
AGORA_SECRET_KEY = os.environ.get("AGORA_GCS_SECRET_KEY")
BUCKET_NAME = os.environ.get("AGORA_BUCKET_NAME")
SIG_SIP_URI = os.environ.get("SIGNALWIRE_SIP_URI")
SIG_USERNAME = os.environ.get("SIGNALWIRE_USERNAME")
SIG_PASSWORD = os.environ.get("SIGNALWIRE_PASSWORD")

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])

credentials = service_account.Credentials.from_service_account_info(
    service_account_info
)

storage_client = storage.Client(credentials=credentials)

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
            "token": "007eJxTYCiacH7W7OVrW1Q57ZICUp3UnC7nbhe9qJu3m2ka62EnyUYFBnNjg+S01ERLE/O0NJO0ZMukNOPUtLRUo5SklFRTy1QzdZbJmQ2BjAzf058xMjJAIIjPw1CSWlwSn5yRmJeXmsPAAADwwCH9",  # Add RTC token here if your channel requires it
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




# =========================================

# if __name__ == "__main__":
#     app.run(port=5000, debug=True)
