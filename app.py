from flask import Flask, request, render_template, redirect, url_for, session, flash
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv
from forms import SignUpForm, LogInForm, FeedbackForm
from feedbackdisplay import format_schema, generate_table_headers, compare_user_query, humanize_query_error
from helpers import validate_sql_input, execute_sql, log_user_attempt, update_streak_and_xp_if_passed, get_solved_question_ids, update_last_attempted, get_all_questions, upload_profile_pic
from firebasesetup import firebase_admin, bucket, firestore , admin_auth, pyre_auth

import os
db_firestore = firestore.client()
load_dotenv()
if os.getenv('FLASK_ENV', 'production') != 'production':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
import os

app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY")
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static/uploads')
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

@app.context_processor
def inject_globals():
    return {"current_year": datetime.now(timezone.utc).year}

# Simple in-memory rate limit for login attempts per IP
from collections import defaultdict
_login_attempts = defaultdict(lambda: {"count": 0, "ts": datetime.now(timezone.utc)})
_LOGIN_WINDOW_SEC = 300
_LOGIN_MAX_ATTEMPTS = 10


def get_question_data_with_id(id, include_question_data=False):
    questions_ref = db_firestore.collection("questions").document(str(id))
    question = questions_ref.get()
    question_data = question.to_dict()
    question_data['id'] = question.id 
    schema  = format_schema(question_data['schema_sql'])
    headers, expected_output_list = generate_table_headers(question_data['expected_output'])

    if include_question_data:
        return [schema, headers, expected_output_list, question_data]
    return [schema, headers, expected_output_list]


@app.route('/')
def home():
    session.clear()
    return render_template("index.html")

@app.route('/feedback', methods=["GET", "POST"])
def feedback():
    uid = session.get('uid')
    if not uid:
        return redirect(url_for('login'))
    user = session.get('user')
    form = FeedbackForm()
    if form.validate_on_submit():
        like = form.like.data.strip()
        dislike = form.dislike.data.strip()
        hate = form.hate.data.strip()

        user_feedback = {
            'like':like,
            'dislike': dislike,
            'hate' : hate
        }

        feedback_ref = db_firestore.collection("feedback")
        feedback_ref.document(uid).set(user_feedback)
        flash("Thank you for your feedback!", "feedback")
        return redirect(url_for('dashboard'))

    return render_template('feedback.html', form=form, user=user)

@app.route('/roadmap')
def roadmap():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    return render_template('roadmap.html', user=user)

@app.route('/signup', methods=["GET", "POST"])
def signup():
    form = SignUpForm()

    if form.validate_on_submit():
        first_name = form.first_name.data.strip()
        last_name = form.last_name.data.strip()
        email = form.email.data.strip().lower()
        username = form.username.data.strip().lower()
        bio = (form.bio.data or '').strip()
        password = form.password.data.strip()
        profile_pic_file = form.profile_pic.data

        # 1. Check if username already exists in Firestore
        users_ref = db_firestore.collection("users")
        existing = users_ref.where("username", "==", username).limit(1).get()
        if existing:
            flash("Username already taken. Please choose another.", "error")
            return redirect(url_for('signup'))

        existing_email = users_ref.where("email", '==', email).limit(1).get()
        if existing_email:
            flash("Email is already in use. Please choose another or Log in.", "error")
            return redirect(url_for('signup'))
        # 2. Create user in Firebase Auth
        try:
            user_record = admin_auth.create_user(
                email=email,
                password=password,
                display_name=f"{first_name} {last_name}"
            )
            uid = user_record.uid
        except Exception as e:
            flash(f"Error creating user: {str(e)}", "error")
            return redirect(url_for('signup'))

        # 3. Handle profile picture: store profile pic in storage
        try:
            image_url = upload_profile_pic(uid, profile_pic_file, bucket)
        except ValueError as ve:
            flash(str(ve), 'error')
            return redirect(url_for('signup'))

        # 4. Store user profile data in Firestore
        user_profile = {
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
            "email": email,
            "bio": bio,
            "profile_pic": image_url,
            "created_at": datetime.now(timezone.utc),
            "xp": 0,
            "current_streak": 0,
            "last_attempted_at": None,
            "last_correct_date": None
        }
        users_ref.document(uid).set(user_profile)

        # Auto-login the user after successful signup
        try:
            login_user = pyre_auth.sign_in_with_email_and_password(email, password)
            uid = login_user['localId']
            user_doc = db_firestore.collection('users').document(uid).get()
            if user_doc.exists:
                user_profile = user_doc.to_dict()
            else:
                user_profile = None

            user_profile_copy = (user_profile or {}).copy()
            # ensure id field is present and remove any heavy fields
            user_profile_copy['id'] = uid
            user_profile_copy.pop('profile_pic_base64', None)

            session['uid'] = uid
            session['user'] = user_profile_copy
            session['idToken'] = login_user['idToken']
            flash(f"Welcome, {first_name}! Your account has been created.", 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            # If auto-login fails for any reason, fall back to login page
            flash("Account created. Please log in.", 'success')
            return redirect(url_for('login'))

    return render_template("signup.html", form=form)


@app.route('/login', methods=["POST", "GET"])
def login():
    form = LogInForm()

    if form.validate_on_submit():
        # rate limit by remote addr
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        info = _login_attempts[ip]
        now_ts = datetime.now(timezone.utc)
        if (now_ts - info["ts"]).total_seconds() > _LOGIN_WINDOW_SEC:
            info["count"], info["ts"] = 0, now_ts
        info["count"] += 1
        if info["count"] > _LOGIN_MAX_ATTEMPTS:
            flash("Too many login attempts. Please try again later.", 'error')
            return render_template('login.html', form=form)

        email = form.email.data
        password = form.password.data
        
        #Find user by email
        users_ref = db_firestore.collection("users")
        existing = users_ref.where("email", "==", email).limit(1).get()
        if existing:
            try:
                login_user = pyre_auth.sign_in_with_email_and_password(email, password)
            except Exception:
                flash("Invalid email or password. Try again.", 'error')
                return render_template('login.html', form=form)
            
            uid = login_user['localId']
            user_doc = db_firestore.collection('users').document(uid).get()
            if user_doc.exists:
                user_profile = user_doc.to_dict()
            else:
                user_profile = None

            user_profile_copy = user_profile.copy()
            user_profile_copy.pop('profile_pic_base64', None)  # Remove profile pic if it exists
            user_profile_copy['id'] = uid 

            #store session data
            session['uid'] = uid
            session['user'] = user_profile_copy
            session['idToken'] = login_user['idToken']
            _login_attempts[ip] = {"count": 0, "ts": datetime.now(timezone.utc)}
            flash("You were logged in successfully!", 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('There is no registered user with email. Sign up instead.', 'error')

    return render_template('login.html', form=form)


# #ACTUAL HOME-PAGE
@app.route('/dashboard')
def dashboard():
    user = session.get('user')
    print(user)
    if not user:
        return redirect(url_for('login'))
    
    attempts_ref = db_firestore.collection('user_attempts')
    passed_attempts = attempts_ref.where('user_id', '==', user['id'])\
                                 .where('passed', '==', True).stream()

    solved_questions = set()
    for attempt in passed_attempts:
        data = attempt.to_dict()
        solved_questions.add(data['question_id'])
    
    num_questions_solved = len(solved_questions)

    return render_template("dashboard.html", num_questions_solved=num_questions_solved, user=user)


@app.route('/leaderboard')
def leaderboard():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    period = request.args.get('period', 'all')
    period = period if period in { 'all', '30d', '90d' } else 'all'

    users = []
    if period == 'all':
        users_ref = db_firestore.collection('users')
        query = users_ref.where('xp', ">", 0).order_by("xp", direction=firestore.Query.DESCENDING).limit(100)
        leaderboard = query.stream()
        for doc in leaderboard:
            data = doc.to_dict()
            last_dt = data.get('last_correct_date')
            if isinstance(last_dt, datetime):
                last_display = last_dt.strftime('%b %d, %Y')
            else:
                try:
                    last_display = datetime.fromisoformat(str(last_dt)).strftime('%b %d, %Y') if last_dt else None
                except Exception:
                    last_display = None
            users.append({
                "username": data.get("username", "Anonymous"),
                "xp": data.get("xp", 0),
                "streak": data.get("current_streak", 0),
                'last_correct_date_display': last_display,
                'profile_pic':data.get('profile_pic', None)
            })
    else:
        now = datetime.now(timezone.utc)
        window_days = 30 if period == '30d' else 90
        window_start = now - timedelta(days=window_days)

        attempts_ref = db_firestore.collection('user_attempts')
        # Avoid composite index: filter by submitted_at only, then filter passed in code
        attempts_query = attempts_ref.where('submitted_at', '>=', window_start).order_by('submitted_at')
        attempts = attempts_query.stream()

        # Sum xp_awarded per user within window, with fallback for legacy entries
        from collections import defaultdict
        user_xp = defaultdict(int)
        counted_questions_in_window = defaultdict(set)  # uid -> set(question_id) to avoid double-counting within window
        xp_gain_map = {'easy': 10, 'medium': 20, 'hard': 50}
        for a in attempts:
            a_data = a.to_dict()
            if not a_data.get('passed'):
                continue
            uid = a_data.get('user_id')
            qid = a_data.get('question_id')
            if not uid:
                continue
            # Prefer stored xp_awarded; otherwise, derive from difficulty once per (user, question) in window
            xp_awarded = a_data.get('xp_awarded')
            if xp_awarded is None:
                # Legacy attempt without xp_awarded; approximate from difficulty and dedupe per question in window
                if qid and qid in counted_questions_in_window[uid]:
                    continue
                diff = str(a_data.get('difficulty', '')).lower()
                xp_awarded = xp_gain_map.get(diff, 0)
                if qid:
                    counted_questions_in_window[uid].add(qid)
            try:
                xp_awarded = int(xp_awarded or 0)
            except Exception:
                xp_awarded = 0
            user_xp[uid] += xp_awarded

        # Fetch top users' profiles
        top = sorted(user_xp.items(), key=lambda kv: kv[1], reverse=True)[:100]
        for uid, xp in top:
            user_doc = db_firestore.collection('users').document(uid).get()
            data = user_doc.to_dict() if user_doc.exists else {}
            last_dt = data.get('last_correct_date')
            if isinstance(last_dt, datetime):
                last_display = last_dt.strftime('%b %d, %Y')
            else:
                try:
                    last_display = datetime.fromisoformat(str(last_dt)).strftime('%b %d, %Y') if last_dt else None
                except Exception:
                    last_display = None
            users.append({
                "username": (data or {}).get("username", "Anonymous"),
                "xp": xp,
                "streak": (data or {}).get("current_streak", 0),
                'last_correct_date_display': last_display,
                'profile_pic': (data or {}).get('profile_pic', None)
            })

    return render_template('leaderboard.html', users=users, user=user, period=period)


@app.route('/questions')
def questions():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    difficulty = request.args.get("difficulty", 'all').lower()
    search = request.args.get('query', '').lower().strip()
    try:
        page = int(request.args.get('page', 1))
    except Exception:
        page = 1
    page = max(page, 1)
    page_size = 50

    # Fetch all questions, then filter/sort client-side by numeric id
    q_ref = db_firestore.collection('questions')
    docs = q_ref.stream()
    items = []
    for doc in docs:
        data = doc.to_dict()
        # Prefer document ID as numeric identifier
        data_id = str(doc.id)
        data['id'] = data_id
        try:
            data['id_num'] = int(data_id)
        except Exception:
            # Push non-numeric IDs to the end
            data['id_num'] = 10**9
        items.append(data)

    if difficulty != 'all':
        items = [it for it in items if str(it.get('difficulty', '')).lower() == difficulty]

    if search:
        items = [
            it for it in items
            if search == str(it.get('id', '')).lower()
            or search in str(it.get('tags', '')).lower()
        ]

    # Sort by numeric id ascending
    items.sort(key=lambda it: it.get('id_num', 10**9))

    # Paginate
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]
    total_pages = (total + page_size - 1) // page_size if total else 1
    has_prev = page > 1
    has_next = end < total

    solved_ids = get_solved_question_ids(db_firestore, user['id'])
    return render_template(
        'questions.html',
        questions=page_items,
        difficulty=difficulty,
        query=search,
        user=user,
        solved_ids=solved_ids,
        page=page,
        has_prev=has_prev,
        has_next=has_next,
        total_pages=total_pages
    )

@app.route('/question/<int:id>')
def view_question(id):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    schema, headers, expected_output_list, question_data= get_question_data_with_id(id,True)
    return render_template('solve-interface.html', question=question_data, schema=schema, headers=headers, expected_output_list=expected_output_list, user=user, next_question_id= int(question_data['id']) + 1)


@app.route("/run-sql/<int:id>", methods=["POST", "GET"])
def run_sql(id):
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    user_query = request.form.get('sql_query')
    is_valid, error_message = validate_sql_input(user_query)

    schema, headers, expected_output_list, question_data= get_question_data_with_id(id,True)
    seed_data_sql = question_data['seed_data_sql']

    #Handle invalid SQL input early
    if not is_valid:
        return render_template('solve-interface.html',
                               next_question_id= int(question_data['id']) + 1,
                           question=question_data,
                           schema=schema,
                           result=None,
                           headers=[],
                           expected_output_list=expected_output_list,
                           user_query='',
                           error_message=error_message,
                           user=user)

    try:
        #Evaluate query and determine correctness
        rows, columns =  execute_sql(schema, seed_data_sql, user_query)
        feedback = compare_user_query(expected_output_list, rows)
        passed = feedback[0] == "success"

        attempts_ref = db_firestore.collection('user_attempts')
        query = attempts_ref.where('user_id', '==', str(user['id']))\
                    .where('question_id', '==', question_data['id'])\
                    .where('passed', '==', True)\
                    .limit(1).stream()
        
        correct_past_attempt = next(query, None)

        if correct_past_attempt and passed:
            # Already solved before: log attempt with 0 XP to reflect activity without double-counting
            log_user_attempt(db_firestore, user, question_data, passed, xp_awarded=0)
            update_last_attempted(db_firestore, user['id'])
            #Don't award XP or update streak again
            return render_template('solve-interface.html',
                                   next_question_id= int(question_data['id']) + 1,
                                question=question_data,
                                schema=schema,
                                result=rows,
                                headers=columns,
                                expected_output_list=expected_output_list,
                                question_headers=headers,
                                user_query=user_query,
                                feedback=('info', 'Good job! You solved this before, but keep practicing.'),
                                user=user)
        
        if passed:
            xp_gain_map = {'easy': 10, 'medium': 20, 'hard': 50}
            xp_gain = xp_gain_map.get(question_data['difficulty'], 0)
            update_streak_and_xp_if_passed(db_firestore, user, xp_gain)
            log_user_attempt(db_firestore, user, question_data, passed, xp_awarded=xp_gain)
            flash(f"🎉 +{xp_gain} XP", 'success')
            # Step 2: Re-fetch updated user data from Firestore
            user_ref = db_firestore.collection('users').document(user['id'])
            updated_user_doc = user_ref.get()

            if updated_user_doc.exists:
                updated_user = updated_user_doc.to_dict()
                updated_user['id'] = session['uid']
                # Step 3: Update session or local user variable with fresh data
                session['user'] = updated_user


        return render_template('solve-interface.html',
                                question=question_data,
                                next_question_id= int(question_data['id']) + 1,
                                schema=schema,
                                result=rows,
                                headers=columns,
                                expected_output_list=expected_output_list,
                                question_headers=headers,
                                user_query=user_query,
                                feedback=feedback,
                                user=user)
        
    
    except Exception as e:
        log_user_attempt(db_firestore, user, question_data, passed=False, xp_awarded=0)
        update_last_attempted(db_firestore, user['id'])
        return render_template('solve-interface.html',
                               next_question_id= int(question_data['id']) + 1,
                           question=question_data,
                           schema=format_schema(schema),
                           result=None,
                           headers=[],
                           expected_output_list=expected_output_list,
                           user_query=user_query,
                           error_message=humanize_query_error(str(e)),
                           user=user)



@app.route('/profile', methods=["POST", "GET"])
def profile():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))

    attempts_ref = db_firestore.collection('user_attempts')
    passed_attempts = attempts_ref.where('user_id', '==', user['id'])\
                                 .where('passed', '==', True).stream()

    solved_questions = set()
    for attempt in passed_attempts:
        data = attempt.to_dict()
        solved_questions.add(data['question_id'])
    
    num_questions_solved = len(solved_questions)

    user_ref = db_firestore.collection('users').document(user['id'])
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        # Provide string display for created_at if present
        created = user_data.get('created_at')
        if isinstance(created, datetime):
            user_data['created_at_display'] = created.strftime('%B %d, %Y')
        else:
            try:
                user_data['created_at_display'] = datetime.fromisoformat(str(created)).strftime('%B %d, %Y') if created else None
            except Exception:
                user_data['created_at_display'] = None
    else:
        user_data = None

    return render_template('profile.html', user=user, num_questions_solved=num_questions_solved, user_data=user_data)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == '__main__':
    print("App is starting")
    app.run(host="0.0.0.0", debug=True, port=1234)

# all_questions = get_all_questions(db_firestore)
# print(all_questions)
