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
    channel = request.json["channel"]
    uid = request.json.get("uid", "0")

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/acquire"

    payload = {
        "cname": channel,
        "uid": uid,
        "clientRequest": {}
    }

    r = requests.post(url, headers=agora_auth(), json=payload)
    return jsonify(r.json())


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
            "token": "",
            "recordingConfig": {
                "maxIdleTime": 30,
                "streamTypes": 0,
                "channelType": 0,
                "videoStreamType": 0,
                "postponeTranscoding": True
            },
            "recordingFileConfig": {
                "avFileType": ["m4a"]
            },
            "storageConfig": {
                "vendor": 2,  # Google Cloud
                "region": 0,
                "bucket": BUCKET_NAME,
                "accessKey": AGORA_ACCESS_KEY,
                "secretKey": AGORA_SECRET_KEY,
                "fileNamePrefix": ["records"]
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

    url = f"https://api.agora.io/v1/apps/{APP_ID}/cloud_recording/resourceid/{resource_id}/sid/{sid}/mode/mix/stop"

    payload = {
        "cname": channel,
        "uid": uid,
        "clientRequest": {}
    }

    r = requests.post(url, headers=agora_auth(), json=payload)
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



# =========================================

# if __name__ == "__main__":
#     app.run(port=5000, debug=True)
