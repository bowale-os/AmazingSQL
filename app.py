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
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
import os

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///instance/amazingsql.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY")
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static/uploads')


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

    return render_template('feedback.html', form=form)

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
        image_url = upload_profile_pic(uid, profile_pic_file,bucket)

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
        # login_user = pyre_auth.sign_in_with_email_and_password(email, password)

        flash("Account created successfully! Log in", "success")
        return redirect(url_for('login'))

    return render_template("signup.html", form=form)


@app.route('/login', methods=["POST", "GET"])
def login():
    form = LogInForm()

    if form.validate_on_submit():
        email = form.email.data
        password = form.password.data
        
        #Find user by email
        users_ref = db_firestore.collection("users")
        existing = users_ref.where("email", "==", email).limit(1).get()
        if existing:
            try:
                login_user = pyre_auth.sign_in_with_email_and_password(email, password)
            except:
                flash("Invalid email or password, try again.", 'error')
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
            flash("You were logged in successfully!", 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('There was no registered user with email. Sign up instead.', 'error')

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
    
    users_ref = db_firestore.collection('users')
    query = users_ref.where('xp', ">", 0).order_by("xp", direction=firestore.Query.DESCENDING)
    leaderboard = query.stream()

    users = []
    for doc in leaderboard:
        data = doc.to_dict()
        users.append({
            "username": data.get("username", "Anonymous"),
            "xp": data.get("xp", 0),
            "streak": data.get("current_streak", 0),
            'last_correct_date': data.get('last_correct_date'),
            'profile_pic':data.get('profile_pic', None)
        })

    return render_template('leaderboard.html', users=users, user=user)


@app.route('/questions')
def questions():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    
    all_questions = get_all_questions(db_firestore)
    difficulty = request.args.get("difficulty", 'all').lower()
    query = request.args.get('query', '').lower()

    if query:
        all_questions = [
            q for q in all_questions
            if query.lower() == q['id'].lower() or
            query.lower() in q.get('tags', '').lower()
        ]
    
    if difficulty != 'all':
        all_questions = [
            q for q in all_questions
            if difficulty == q.get('difficulty', '').lower()
        ]

    all_questions_sorted = sorted(all_questions, key=lambda q: int(q['id']))
    solved_ids = get_solved_question_ids(db_firestore, user['id'])
    return render_template('questions.html', questions=all_questions_sorted[:40], difficulty=difficulty, query=query, user=user, solved_ids=solved_ids)

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
                           error_message=error_message)

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
            log_user_attempt(db_firestore ,user, question_data, passed)
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
                                feedback=('info', 'Good job! You solved this before, but keep practicing.'))
        
        if passed:
            xp_gain_map = {'easy': 10, 'medium': 20, 'hard': 50}
            xp_gain = xp_gain_map.get(question_data['difficulty'], 0)
            update_streak_and_xp_if_passed(db_firestore, user, xp_gain)
            log_user_attempt(db_firestore ,user, question_data, passed)
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
                                feedback=feedback)
        
    
    except Exception as e:
        log_user_attempt(db_firestore ,user, question_data, passed=False)
        update_last_attempted(db_firestore, user['id'])
        return render_template('solve-interface.html',
                               next_question_id= int(question_data['id']) + 1,
                           question=question_data,
                           schema=format_schema(schema),
                           result=None,
                           headers=[],
                           expected_output_list=expected_output_list,
                           user_query=user_query,
                           error_message=humanize_query_error(str(e)))



@app.route('/profile', methods=["POST", "GET"])
def profile():
    user = session['user']
    if not user:
        return redirect(url_for('login'))
    return render_template('profile.html', user=user)


# @app.route('/add_bio', methods=["POST", "GET"])
# @login_required
# def add_bio():
#     form = BioForm()

#     if form.validate_on_submit():
#         current_user.bio = form.bio.data
#         db.session.commit()
#         flash("Bio updated successfully!", "success")
#         return redirect(url_for('profile'))

#     #prepopulate if user already has a bio
#     if request.method == "GET":
#         form.bio.data = current_user.bio

#     return render_template('add-bio.html', form=form)


# @app.route('/edit-profile', methods=["POST", "GET"])
# @login_required
# def edit_profile():
#     # form = EditProfileForm()
#     # display_name = form.display_name.data
#     # bio = form.get('bio', None)
#     # profile_pic = form.get('profile_pic', '')
#     return render_template('edit-profile.html')





@app.route('/logout')
def logout():
    # user = session['user']
    # if not user:
    # app.logger.info(f"Logging out user: {current_user.get_id()}")
    # if google.authorized and google.token:
    #     try:
    #         token = google.token["access_token"]
    #         resp = google.post(
    #             "https://oauth2.googleapis.com/revoke",
    #             params={'token': token},
    #             headers={'content-type': 'application/x-www-form-urlencoded'}
    #         )
    #         if resp.status_code == 200:
    #             app.logger.info("Successfully revoked Google token.")
    #         else:
    #             app.logger.warning(f"Failed to revoke token: {resp.text}")
    #     except Exception as e:
    #         app.logger.warning(f"Error revoking token: {e}")

    session.clear()
    return redirect(url_for("home"))


if __name__ == '__main__':
    print("App is starting")
    app.run(host="0.0.0.0", debug=True, port=1234)

# all_questions = get_all_questions(db_firestore)
# print(all_questions)
