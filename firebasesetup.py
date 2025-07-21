import os
import json
import firebase_admin
from dotenv import load_dotenv
import pyrebase

from firebase_admin import credentials, firestore, storage, initialize_app

load_dotenv()

# Load service account JSON string from environment variable
service_account_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
if not service_account_json:
    raise ValueError("FIREBASE_SERVICE_ACCOUNT_JSON env var is missing!")

service_account_info = json.loads(service_account_json)

# Initialize Firebase Admin app only once
if not firebase_admin._apps:
    cred = credentials.Certificate(service_account_info)
    firebase_admin_app = initialize_app(cred, {
        'storageBucket': os.getenv('FIREBASE_STORAGE_BUCKET')
    })
else:
    firebase_admin_app = firebase_admin.get_app()

# Use the app instance to get the storage bucket
bucket = storage.bucket(app=firebase_admin_app)

# Firebase config for pyrebase (client SDK)
firebase_config = {
    'apiKey': os.getenv('FIREBASE_API_KEY'),
    'authDomain': os.getenv('FIREBASE_AUTH_DOMAIN'),
    'projectId': os.getenv('FIREBASE_PROJECT_ID'),
    'storageBucket': os.getenv('FIREBASE_STORAGE_BUCKET'),
    'messagingSenderId': os.getenv('FIREBASE_MESSAGING_SENDER_ID'),
    'appId': os.getenv('FIREBASE_APP_ID'),
    'measurementId': os.getenv('FIREBASE_MEASUREMENT_ID'),
    'databaseURL': os.getenv('FIREBASE_DATABASE_URL'),  # Use Realtime DB URL here
}

# Initialize pyrebase client
firebase = pyrebase.initialize_app(firebase_config)
auth = firebase.auth()
realtime_db = firebase.database()
