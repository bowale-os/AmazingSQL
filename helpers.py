from datetime import datetime, timedelta, timezone, date
import uuid
from io import BytesIO
from PIL import Image, UnidentifiedImageError
import sqlite3
import re

def log_user_attempt(db, user, question_data, passed, xp_awarded: int = 0):
    # Log attempt to Firestore, pass or fail
    user_id = user.get("id")  # 🔐 safely grab it
    if not user_id:
        raise ValueError("User object does not have an 'id' key")
    
    attempt_data = {
        "user_id": user['id'],
        "question_id": question_data['id'],
        "difficulty": question_data['difficulty'],
        "passed": passed,
        "submitted_at": datetime.now(timezone.utc),
        "xp_awarded": int(xp_awarded or 0)
    }

    try:
        db.collection("user_attempts").add(attempt_data)
    except Exception as e:
        print(f"Error logging attempt: {e}")   

def execute_sql(schema_sql, seed_sql, user_query):
    rows, columns = [], []
    with sqlite3.connect(':memory:') as conn:
        cursor = conn.cursor()
        try:
            cursor.executescript(schema_sql)
            cursor.executescript(seed_sql)
            conn.commit()
        except sqlite3.DatabaseError as e:
            print(f"Error executing SQL: {e}")
            return [], []
        try:
            cursor.execute(user_query)
            if user_query.strip().lower().startswith("select"):
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
            else:
                rows, columns = [], []

        except sqlite3.OperationalError as e:
            return [], [], f"SQL syntax error: {e}"
        except sqlite3.ProgrammingError as e:
            return [], [], f"Programming error: {e}"
        except Exception as e:
            return [], [], f"Unexpected error: {e}"

    return rows, columns



def validate_sql_input(user_query, allowed_types=("select",)):
    """
    Validates a user-submitted SQL query for use in a sandboxed in-memory SQLite context.
    Returns: (is_valid: bool, error_message: str or None)
    """
    if not user_query or not user_query.strip():
        return False, "Please enter a SQL query."

    query = user_query.strip()
    lowered = query.lower()

    # Allow only specific SQL commands (default: SELECT only)
    statement_type = lowered.split()[0]
    if statement_type not in allowed_types:
        return (
            False,
            f"Only the following commands are allowed: {', '.join(allowed_types).upper()}. Please start your query with this."
        )

    # Disallow multiple statements (semi-colon injection)
    if ';' in lowered[:-1]:  # Allow a trailing semicolon for style
        return (
            False,
            "Only single SQL statements are allowed. Please submit one query at a time."
        )

    # Disallow destructive, managing, or privacy-violating SQL keywords
    forbidden_patterns = [
        r"\bdrop\b", r"\bdelete\b", r"\binsert\b", r"\bupdate\b", r"\balter\b", r"\btruncate\b",
        r"\battach\b", r"\bdetach\b", r"\bpragma\b", r"\bcreate\s+user\b", r"\bgrant\b",
        r"\brevoke\b"
    ]
    for pat in forbidden_patterns:
        if re.search(pat, lowered):
            return (
                False,
                "Modifying, deleting, or managing the database structure is not allowed. Please submit safe queries."
            )

    # Require a FROM clause in SELECTs
    if statement_type == "select" and "from" not in lowered:
        return (
            False,
            "Your SELECT query seems to be missing a FROM clause. Example: SELECT * FROM question_table"
        )

    # Minimum length: prevent single-word or incomplete queries
    if len(query.split()) < 3:
        return (
            False,
            "Your query looks too short or incomplete. Please provide a full SQL statement, e.g., SELECT column FROM table."
        )

    # Basic pattern check for balanced parentheses
    if query.count('(') != query.count(')'):
        return (
            False,
            "Your query has unbalanced parentheses. Please check your brackets."
        )

    return True, None


def update_streak_and_xp_if_passed(db, user, xp_gain):
    now = datetime.now(timezone.utc)
    print("UPDATE! UPDATE! Called update_streak_and_xp_if_passed")
    try:
        user_ref = db.collection('users').document(user['id'])
        user_doc = user_ref.get()

        if not user_doc.exists:
            print(f"User {user['id']} not found in Firestore.")
            return
        
        user_data = user_doc.to_dict()
        print(f"Current user data from Firestore: {user_data}")

        # Get current values
        current_xp = user_data.get('xp', 0)
        current_streak = user_data.get('current_streak', 0)
        last_correct = user_data.get('last_correct_date')

        print(f"Current XP: {current_xp}, Current Streak: {current_streak}, Last Correct Date: {last_correct}")

        # Update XP
        new_xp = current_xp + xp_gain
        print(f"XP Gain: {xp_gain}, New XP: {new_xp}")

        today = date.today()

        if last_correct:
            if isinstance(last_correct, str):
                print(f"Last correct date is string, converting from ISO format: {last_correct}")
                last_correct = datetime.fromisoformat(last_correct).date()
            elif isinstance(last_correct, datetime):
                last_correct = last_correct.date()
            else:
                print(f"Unexpected last_correct type: {type(last_correct)}")
                last_correct = None

        print(f"Parsed last_correct date: {last_correct}")

        # Calculate new streak
        if last_correct is None:
            new_streak = 1
            print("No last_correct date found, starting streak at 1")
        else:
            diff = (today - last_correct).days
            print(f"Days since last correct: {diff}")
            if diff == 0:
                print("Already solved today — no update and xp addition needed")
                user_ref.update({
                    'xp': new_xp,
                    'last_correct_date': now,
                    'last_attempted_at': datetime.now(timezone.utc)
    }                )
                return
            elif diff == 1:
                new_streak = current_streak + 1
                print(f"Incrementing streak to: {new_streak}")
            else:
                new_streak = 1
                print("Streak broken, resetting streak to 1")

        # Commit updates
        
        print(f"Updating Firestore with xp={new_xp}, streak={new_streak}, last_correct_date={now.date()}, last_attempted_at={now}")
        user_ref.update({
            'xp': new_xp,
            'current_streak': new_streak,
            'last_correct_date': now,  # <-- note the parentheses here
            'last_attempted_at': now
        })

        print("Update successful!")

    except Exception as e:
        print(f"Ran into this error: {e}")
        return f"Ran into this error: {e}"


def update_last_attempted(db, user_id):
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()

        if not user_doc.exists:
            print(f"User {user_id} not found in Firestore.")
            return

        now = datetime.now(timezone.utc)
        user_ref.update({
            'last_attempted_at': now
        })

    except Exception as e:
        return f"Ran into this error: {e}"

def upload_profile_pic(user_id, profile_pic_file, bucket, max_bytes: int = 5 * 1024 * 1024):
    if not profile_pic_file:
        return None

    # Enforce max size
    data = profile_pic_file.read()
    if len(data) > max_bytes:
        raise ValueError("Profile picture is too large. Please upload an image under 5MB.")

    # Detect format using Pillow; fall back to extension
    ext = None
    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()  # validate file integrity
            fmt = (img.format or '').upper()
            if fmt == 'JPEG':
                ext = 'jpg'
            elif fmt == 'PNG':
                ext = 'png'
    except UnidentifiedImageError:
        ext = None

    if not ext:
        # try from filename as a fallback check
        filename = getattr(profile_pic_file, 'filename', '') or ''
        if filename.lower().endswith(('.jpg', '.jpeg')):
            ext = 'jpg'
        elif filename.lower().endswith('.png'):
            ext = 'png'

    if ext not in {"jpg", "png"}:
        raise ValueError("Unsupported image type. Please upload a JPG or PNG image.")

    unique_key = f"profile_pics/{user_id}_{uuid.uuid4().hex}.{ext}"
    blob = bucket.blob(unique_key)
    blob.upload_from_string(data, content_type=f"image/{'jpeg' if ext=='jpg' else 'png'}")
    blob.make_public()
    return blob.public_url



def get_solved_question_ids(db, user_id):
    attempts_ref = db.collection('user_attempts')
    solved_attempts = attempts_ref.where('user_id', '==', user_id)\
                             .where('passed', '==', True).stream()

    solved_question_ids = set()
    for attempt in solved_attempts:
        data = attempt.to_dict()
        solved_question_ids.add(data['question_id'])
    return solved_question_ids



def get_all_questions(db):
    questions_ref = db.collection('questions')
    docs = questions_ref.stream()

    questions = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id  # include Firestore doc ID
        questions.append(data)
    return questions


