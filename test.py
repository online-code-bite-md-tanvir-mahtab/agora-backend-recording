from google.cloud import storage
from datetime import timedelta

# Path to your service account JSON file
SERVICE_ACCOUNT_FILE = "service_account.json"

# Your bucket name
BUCKET_NAME = "agora-recordings-ta"

# The file names reported by Agora
files_to_check = [
    "05073b49864bbe2ff3d1a9b41580fde0_test_channel.m3u8",
    "05073b49864bbe2ff3d1a9b41580fde0_test_channel_0.mp4"
]

def main():
    # Initialize the client
    client = storage.Client.from_service_account_json(SERVICE_ACCOUNT_FILE)
    bucket = client.bucket(BUCKET_NAME)
    print(f"Checking for files in bucket: {bucket.name}\n")

    for file_name in files_to_check:
        blob = bucket.blob(file_name)

        if blob.exists():
            print(f"✅ Found: {file_name}")
            # Generate a signed URL valid for 1 hour
            url = blob.generate_signed_url(expiration=timedelta(hours=1))
            print(f"Signed URL: {url}\n")
        else:
            print(f"❌ Not found: {file_name}\n")

if __name__ == "__main__":
    main()
