from firebasesetup import firebase_admin
from firebase_admin import firestore
import csv 
db = firestore.client()

with open('amazingsql_questions.csv', newline='', encoding='utf-8') as file:
    reader = csv.DictReader(file)
    for row in reader:
        doc_ref = db.collection('questions').document(row['id'])
        doc_ref.set({
            'title': row['title'],
            'description':row['description'],
            'difficulty':row['difficulty'],
            'tags':row['tags'],
            'schema_sql':row['schema_sql'],
            'seed_data_sql':row['seed_data_sql'],
            'solution':row['solution'],
            'expected_output':row['expected_output']
        })