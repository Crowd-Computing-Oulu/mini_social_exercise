from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import collections
import math 
import json
import sqlite3
import hashlib
import re
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = '123456789' 
DATABASE = 'database.sqlite'

# Load censorship data
# WARNING! The censorship.dat file contains disturbing language when decrypted. 
# If you want to test whether moderation works, 
# you can trigger censorship using these words: 
# tier1badword, tier2badword, tier3badword
ENCRYPTED_FILE_PATH = 'censorship.dat'
fernet = Fernet('xpplx11wZUibz0E8tV8Z9mf-wwggzSrc21uQ17Qq2gg=')
with open(ENCRYPTED_FILE_PATH, 'rb') as encrypted_file:
    encrypted_data = encrypted_file.read()
decrypted_data = fernet.decrypt(encrypted_data)
MODERATION_CONFIG = json.loads(decrypted_data)
TIER1_WORDS = MODERATION_CONFIG['categories']['tier1_severe_violations']['words']
TIER2_PHRASES = MODERATION_CONFIG['categories']['tier2_spam_scams']['phrases']
TIER3_WORDS = MODERATION_CONFIG['categories']['tier3_mild_profanity']['words']
POSITIVE_REACTIONS = ('like', 'love', 'laugh', 'wow')
REC_STOP_WORDS = {
    'this', 'that', 'with', 'from', 'have', 'your', 'about', 'what', 'when',
    'where', 'which', 'will', 'they', 'them', 'there', 'their', 'would',
    'could', 'should', 'into', 'just', 'like', 'some', 'more', 'very',
    'been', 'also', 'than', 'then', 'here'
}
 
 
def _keywords(text):
    """Lower-cased words of 4+ letters, without stop words."""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', (text or '').lower())
    return [w for w in words if w not in REC_STOP_WORDS]

def get_db():
    """
    Connect to the application's configured database. The connection
    is unique for each request and will be reused if this is called
    again.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db


@app.teardown_appcontext
def close_connection(exception):
    """Closes the database again at the end of the request."""
    db = g.pop('db', None)

    if db is not None:
        db.close()


def query_db(query, args=(), one=False, commit=False):
    """
    Queries the database and returns a list of dictionaries, a single
    dictionary, or None. Also handles write operations.
    """
    db = get_db()
    
    # Using 'with' on a connection object implicitly handles transactions.
    # The 'with' statement will automatically commit if successful, 
    # or rollback if an exception occurs. This is safer.
    try:
        with db:
            cur = db.execute(query, args)
        
        # For SELECT statements, fetch the results after the transaction block
        if not commit:
            rv = cur.fetchall()
            return (rv[0] if rv else None) if one else rv
        
        # For write operations, we might want the cursor to get info like lastrowid
        return cur

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

@app.template_filter('datetimeformat')
def datetimeformat(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    else:
        return "N/A"
    return dt.strftime('%b %d, %Y %H:%M')

REACTION_EMOJIS = {
    'like': '❤️', 'love': '😍', 'laugh': '😂',
    'wow': '😮', 'sad': '😢', 'angry': '😠',
}
REACTION_TYPES = list(REACTION_EMOJIS.keys())

def get_personalized_contributions(user_id, limit=5):

    if not user_id:
        return []

    commented_posts = query_db("""
        SELECT p.content
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.user_id = ?
    """, (user_id,))

    reacted_posts = query_db("""
        SELECT p.content
        FROM reactions r
        JOIN posts p ON r.post_id = p.id
        WHERE r.user_id = ?
    """, (user_id,))

    history = commented_posts + reacted_posts

    user_topics = collections.Counter()

    stop_words = {
        'this', 'that', 'with', 'from', 'have', 'your',
        'about', 'what', 'when', 'where', 'which', 'will',
        'they', 'them', 'there', 'their', 'would', 'could',
        'should', 'into', 'just', 'like', 'some', 'more',
        'very', 'been', 'also', 'than', 'then', 'here'
    }

    for item in history:
        words = re.findall(r'\b[a-zA-Z]{4,}\b', item['content'].lower())

        for word in words:
            if word not in stop_words:
                user_topics[word] += 1

    if not user_topics:
        return []

    candidate_posts = query_db("""
        SELECT p.id, p.content, p.created_at,
               u.username, u.id AS user_id
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.user_id != ?
          AND p.id NOT IN (
              SELECT post_id
              FROM reactions
              WHERE user_id = ?
          )
          AND p.id NOT IN (
              SELECT post_id
              FROM comments
              WHERE user_id = ?
          )
        ORDER BY p.created_at DESC
        LIMIT 100
    """, (user_id, user_id, user_id))

    scored_posts = []

    for post in candidate_posts:
        words = set(
            re.findall(
                r'\b[a-zA-Z]{4,}\b',
                post['content'].lower()
            )
        )

        score = sum(user_topics[word] for word in words if word in user_topics)

        if score > 0:
            post_dict = dict(post)
            post_dict['match_score'] = score
            scored_posts.append(post_dict)

    scored_posts.sort(
        key=lambda item: (item['match_score'], item['created_at']),
        reverse=True
    )

    return scored_posts[:limit]


def get_contribution_progress(user_id):
   
    if not user_id:
        return {
            'points': 0,
            'goal': 10,
            'remaining': 10,
            'percentage': 0
        }

    week_start = datetime.now() - timedelta(days=7)

    posts = query_db("""
        SELECT COUNT(*) AS count
        FROM posts
        WHERE user_id = ?
          AND created_at >= ?
    """, (user_id, week_start))

    comments = query_db("""
        SELECT COUNT(*) AS count
        FROM comments
        WHERE user_id = ?
          AND created_at >= ?
    """, (user_id, week_start))

    reactions = query_db("""
        SELECT COUNT(*) AS count
        FROM reactions
        WHERE user_id = ?
    """, (user_id,))

    post_count = posts[0]['count'] if posts else 0
    comment_count = comments[0]['count'] if comments else 0
    reaction_count = reactions[0]['count'] if reactions else 0

    post_points = post_count * 3
    comment_points = comment_count * 2
    reaction_points = reaction_count

    points = post_points + comment_points + reaction_points

    goal = 10

    percentage = min(
        100,
        int((points / goal) * 100)
    )

    remaining = max(
        0,
        goal - points
    )

    return {
        'points': points,
        'goal': goal,
        'remaining': remaining,
        'percentage': percentage
    }

def get_friend_suggestions(user_id, limit=5):

    if not user_id:
        return []

    suggestions = query_db("""
        SELECT
            candidate.id,
            candidate.username,
            candidate.profile,
            COUNT(DISTINCT connection.followed_id) AS mutual_count
        FROM follows connection
        JOIN follows second_connection
            ON connection.followed_id = second_connection.follower_id
        JOIN users candidate
            ON candidate.id = second_connection.followed_id
        WHERE connection.follower_id = ?
          AND candidate.id != ?
          AND candidate.id NOT IN (
              SELECT followed_id
              FROM follows
              WHERE follower_id = ?
          )
        GROUP BY candidate.id, candidate.username, candidate.profile
        ORDER BY mutual_count DESC
        LIMIT ?
    """, (user_id, user_id, user_id, limit))

    return suggestions

@app.route('/')
def feed():
    #  1. Get Pagination and Filter Parameters 
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    sort = request.args.get('sort', 'new').lower()
    show = request.args.get('show', 'all').lower()
    
    # Define how many posts to show per page
    POSTS_PER_PAGE = 10
    offset = (page - 1) * POSTS_PER_PAGE

    current_user_id = session.get('user_id')

    contribution_suggestions = []

    contribution_progress = {
        'points': 0,
        'goal': 10,
        'remaining': 10,
        'percentage': 0
    }

    friend_suggestions = []

    if current_user_id:
        contribution_suggestions = get_personalized_contributions(
            current_user_id
        )

        contribution_progress = get_contribution_progress(
            current_user_id
        )

        friend_suggestions = get_friend_suggestions(
            current_user_id
        )
    params = []

    #  2. Build the Query 
    where_clause = ""
    if show == 'following' and current_user_id:
        where_clause = "WHERE p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?)"
        params.append(current_user_id)

    # Add the pagination parameters to the query arguments
    pagination_params = (POSTS_PER_PAGE, offset)

    if sort == 'popular':
        query = f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id as user_id,
                   IFNULL(r.total_reactions, 0) as total_reactions
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN (
                SELECT post_id, COUNT(*) as total_reactions FROM reactions GROUP BY post_id
            ) r ON p.id = r.post_id
            {where_clause}
            ORDER BY total_reactions DESC, p.created_at DESC
            LIMIT ? OFFSET ?
        """
        final_params = params + list(pagination_params)
        posts = query_db(query, final_params)
    elif sort == 'recommended':
        posts = recommend(current_user_id, show == 'following' and current_user_id)
    else:  # Default sort is 'new'
        query = f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id as user_id
            FROM posts p
            JOIN users u ON p.user_id = u.id
            {where_clause}
            ORDER BY p.created_at DESC
            LIMIT ? OFFSET ?
        """
        final_params = params + list(pagination_params)
        posts = query_db(query, final_params)

    posts_data = []
    for post in posts:
        # Determine if the current user follows the poster
        followed_poster = False
        if current_user_id and post['user_id'] != current_user_id:
            follow_check = query_db(
                'SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?',
                (current_user_id, post['user_id']),
                one=True
            )
            if follow_check:
                followed_poster = True

        # Determine if the current user reacted to this post and with what reaction
        user_reaction = None
        if current_user_id:
            reaction_check = query_db(
                'SELECT reaction_type FROM reactions WHERE user_id = ? AND post_id = ?',
                (current_user_id, post['id']),
                one=True
            )
            if reaction_check:
                user_reaction = reaction_check['reaction_type']

        reactions = query_db('SELECT reaction_type, COUNT(*) as count FROM reactions WHERE post_id = ? GROUP BY reaction_type', (post['id'],))
        comments_raw = query_db('SELECT c.id, c.content, c.created_at, u.username, u.id as user_id FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.created_at ASC', (post['id'],))
        post_dict = dict(post)
        post_dict['content'], _ = moderate_content(post_dict['content'])
        comments_moderated = []
        for comment in comments_raw:
            comment_dict = dict(comment)
            comment_dict['content'], _ = moderate_content(comment_dict['content'])
            comments_moderated.append(comment_dict)
        posts_data.append({
            'post': post_dict,
            'reactions': reactions,
            'user_reaction': user_reaction,
            'followed_poster': followed_poster,
            'comments': comments_moderated
        })

    #  4. Render Template with Pagination Info 
    return render_template('feed.html.j2', 
                           posts=posts_data, 
                           current_sort=sort,
                           current_show=show,
                           page=page, # Pass current page number
                           per_page=POSTS_PER_PAGE, # Pass items per page
                           reaction_emojis=REACTION_EMOJIS,
                           reaction_types=REACTION_TYPES,
                           contribution_suggestions=contribution_suggestions,
                           contribution_progress=contribution_progress,
                           friend_suggestions=friend_suggestions)

@app.route('/posts/new', methods=['POST'])
def add_post():
    """Handles creating a new post from the feed."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to create a post.', 'danger')
        return redirect(url_for('login'))

    access = get_access_level(user_id)
    if not access['can_post']:
        flash(access['reason'], 'warning')
        return redirect(url_for('sandbox') if access['level'] == 0 else url_for('feed'))

    # Get content from the submitted form
    content = request.form.get('content')

    # Pass the user's content through the moderation function
    moderated_content = content

    # Basic validation to ensure post is not empty
    if moderated_content and moderated_content.strip():
        db = get_db()
        db.execute('INSERT INTO posts (user_id, content) VALUES (?, ?)',
                   (user_id, moderated_content))
        db.commit()
        flash('Your post was successfully created!', 'success')
    else:
        # This will catch empty posts or posts that were fully censored
        flash('Post cannot be empty or was fully censored.', 'warning')

    # Redirect back to the main feed to see the new post
    return redirect(url_for('feed'))
    
    
@app.route('/posts/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    """Handles deleting a post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to delete a post.', 'danger')
        return redirect(url_for('login'))

    # Find the post in the database
    post = query_db('SELECT id, user_id FROM posts WHERE id = ?', (post_id,), one=True)

    # Check if the post exists and if the current user is the owner
    if not post:
        flash('Post not found.', 'danger')
        return redirect(url_for('feed'))

    if post['user_id'] != user_id:
        # Security check: prevent users from deleting others' posts
        flash('You do not have permission to delete this post.', 'danger')
        return redirect(url_for('feed'))

    # If all checks pass, proceed with deletion
    db = get_db()
    # To maintain database integrity, delete associated records first
    db.execute('DELETE FROM comments WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM reactions WHERE post_id = ?', (post_id,))
    # Finally, delete the post itself
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()

    flash('Your post was successfully deleted.', 'success')
    # Redirect back to the page the user came from, or the feed as a fallback
    return redirect(request.referrer or url_for('feed'))

@app.route('/u/<username>')
def user_profile(username):
    """Displays a user's profile page with moderated bio, posts, and latest comments."""
    
    user_raw = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user_raw:
        abort(404)

    user = dict(user_raw)
    moderated_bio, _ = moderate_content(user.get('profile', ''))
    user['profile'] = moderated_bio

    posts_raw = query_db('SELECT id, content, user_id, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC', (user['id'],))
    posts = []
    for post_raw in posts_raw:
        post = dict(post_raw)
        moderated_post_content, _ = moderate_content(post['content'])
        post['content'] = moderated_post_content
        posts.append(post)

    comments_raw = query_db('SELECT id, content, user_id, post_id, created_at FROM comments WHERE user_id = ? ORDER BY created_at DESC LIMIT 100', (user['id'],))
    comments = []
    for comment_raw in comments_raw:
        comment = dict(comment_raw)
        moderated_comment_content, _ = moderate_content(comment['content'])
        comment['content'] = moderated_comment_content
        comments.append(comment)

    followers_count = query_db('SELECT COUNT(*) as cnt FROM follows WHERE followed_id = ?', (user['id'],), one=True)['cnt']
    following_count = query_db('SELECT COUNT(*) as cnt FROM follows WHERE follower_id = ?', (user['id'],), one=True)['cnt']

    #  NEW: CHECK FOLLOW STATUS 
    is_currently_following = False # Default to False
    current_user_id = session.get('user_id')
    
    # We only need to check if a user is logged in
    if current_user_id:
        follow_relation = query_db(
            'SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?',
            (current_user_id, user['id']),
            one=True
        )
        if follow_relation:
            is_currently_following = True
    # --

    return render_template('user_profile.html.j2', 
                           user=user, 
                           posts=posts, 
                           comments=comments,
                           followers_count=followers_count, 
                           following_count=following_count,
                           is_following=is_currently_following)
    

@app.route('/u/<username>/followers')
def user_followers(username):
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        abort(404)
    followers = query_db('''
        SELECT u.username
        FROM follows f
        JOIN users u ON f.follower_id = u.id
        WHERE f.followed_id = ?
    ''', (user['id'],))
    return render_template('user_list.html.j2', user=user, users=followers, title="Followers of")

@app.route('/u/<username>/following')
def user_following(username):
    user = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)
    if not user:
        abort(404)
    following = query_db('''
        SELECT u.username
        FROM follows f
        JOIN users u ON f.followed_id = u.id
        WHERE f.follower_id = ?
    ''', (user['id'],))
    return render_template('user_list.html.j2', user=user, users=following, title="Users followed by")

@app.route('/posts/<int:post_id>')
def post_detail(post_id):
    """Displays a single post and its comments, with content moderation applied."""
    
    post_raw = query_db('''
        SELECT p.id, p.content, p.created_at, u.username, u.id as user_id
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
    ''', (post_id,), one=True)

    if not post_raw:
        # The abort function will stop the request and show a 404 Not Found page.
        abort(404)

    #  Moderation for the Main Post 
    # Convert the raw database row to a mutable dictionary
    post = dict(post_raw)
    # Unpack the tuple from moderate_content, we only need the moderated content string here
    moderated_post_content, _ = moderate_content(post['content'])
    post['content'] = moderated_post_content

    #  Fetch Reactions (No moderation needed) 
    reactions = query_db('''
        SELECT reaction_type, COUNT(*) as count
        FROM reactions
        WHERE post_id = ?
        GROUP BY reaction_type
    ''', (post_id,))

    #  Fetch and Moderate Comments 
    comments_raw = query_db('SELECT c.id, c.content, c.created_at, u.username, u.id as user_id FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.created_at ASC', (post_id,))
    
    comments = [] # Create a new list for the moderated comments
    for comment_raw in comments_raw:
        comment = dict(comment_raw) # Convert to a dictionary
        # Moderate the content of each comment
        print(comment['content'])
        moderated_comment_content, _ = moderate_content(comment['content'])
        comment['content'] = moderated_comment_content
        comments.append(comment)

    # Pass the moderated data to the template
    return render_template('post_detail.html.j2',
                           post=post,
                           reactions=reactions,
                           comments=comments,
                           reaction_emojis=REACTION_EMOJIS,
                           reaction_types=REACTION_TYPES)

@app.route('/about')
def about():
    return render_template('about.html.j2')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html.j2')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        location = request.form.get('location', '')
        birthdate = request.form.get('birthdate', '')
        profile = request.form.get('profile', '')

        hashed_password = generate_password_hash(password)

        db = get_db()
        cur = db.cursor()
        try:
            cur.execute(
                'INSERT INTO users (username, password, location, birthdate, profile) VALUES (?, ?, ?, ?, ?)',
                (username, hashed_password, location, birthdate, profile)
            )
            db.commit()

            # 1. Get the ID of the user we just created.
            new_user_id = cur.lastrowid

            # 2. Add user info to the session cookie.
            session.clear() # Clear any old session data
            session['user_id'] = new_user_id
            session['username'] = username

            # 3. Flash a welcome message and redirect to the feed.
            flash(f'Welcome, {username}! Your account has been created.', 'success')
            return redirect(url_for('feed')) # Redirect to the main feed/dashboard

        except sqlite3.IntegrityError:
            flash('Username already taken. Please choose another one.', 'danger')
        finally:
            cur.close()
            db.close()
            
    return render_template('signup.html.j2')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db.close()

        # 1. Check if the user exists.
        # 2. If user exists, use check_password_hash to securely compare the password.
        #    This function handles the salt and prevents timing attacks.
        if user and check_password_hash(user['password'], password):
            # Password is correct!
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Logged in successfully.', 'success')
            return redirect(url_for('feed'))
        else:
            # User does not exist or password was incorrect.
            flash('Invalid username or password.', 'danger')
            
    return render_template('login.html.j2')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/posts/<int:post_id>/comment', methods=['POST'])
def add_comment(post_id):
    """Handles adding a new comment to a specific post."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to comment.', 'danger')
        return redirect(url_for('login'))

    # Get content from the submitted form
    content = request.form.get('content')

    # Basic validation to ensure comment is not empty
    if content and content.strip():
        db = get_db()
        db.execute('INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)',
                   (post_id, user_id, content))
        db.commit()
        flash('Your comment was added.', 'success')
    else:
        flash('Comment cannot be empty.', 'warning')

    # Redirect back to the page the user came from (likely the post detail page)
    return redirect(request.referrer or url_for('post_detail', post_id=post_id))

@app.route('/comments/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    """Handles deleting a comment."""
    user_id = session.get('user_id')

    # Block access if user is not logged in
    if not user_id:
        flash('You must be logged in to delete a comment.', 'danger')
        return redirect(url_for('login'))

    # Find the comment and the original post's author ID
    comment = query_db('''
        SELECT c.id, c.user_id, p.user_id as post_author_id
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.id = ?
    ''', (comment_id,), one=True)

    # Check if the comment exists
    if not comment:
        flash('Comment not found.', 'danger')
        return redirect(request.referrer or url_for('feed'))

    # Security Check: Allow deletion if the user is the comment's author OR the post's author
    if user_id != comment['user_id'] and user_id != comment['post_author_id']:
        flash('You do not have permission to delete this comment.', 'danger')
        return redirect(request.referrer or url_for('feed'))

    # If all checks pass, proceed with deletion
    db = get_db()
    db.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()

    flash('Comment successfully deleted.', 'success')
    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('feed'))

@app.route('/react', methods=['POST'])
def add_reaction():
    """Handles adding a new reaction or updating an existing one."""
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to react.", "danger")
        return redirect(url_for('login'))

    post_id = request.form.get('post_id')
    new_reaction_type = request.form.get('reaction')

    if not post_id or not new_reaction_type:
        flash("Invalid reaction request.", "warning")
        return redirect(request.referrer or url_for('feed'))

    db = get_db()

    # Step 1: Check if a reaction from this user already exists on this post.
    existing_reaction = query_db('SELECT id FROM reactions WHERE post_id = ? AND user_id = ?',
                                 (post_id, user_id), one=True)

    if existing_reaction:
        # Step 2: If it exists, UPDATE the reaction_type.
        db.execute('UPDATE reactions SET reaction_type = ? WHERE id = ?',
                   (new_reaction_type, existing_reaction['id']))
    else:
        # Step 3: If it does not exist, INSERT a new reaction.
        db.execute('INSERT INTO reactions (post_id, user_id, reaction_type) VALUES (?, ?, ?)',
                   (post_id, user_id, new_reaction_type))

    db.commit()

    return redirect(request.referrer or url_for('feed'))

@app.route('/unreact', methods=['POST'])
def unreact():
    """Handles removing a user's reaction from a post."""
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to unreact.", "danger")
        return redirect(url_for('login'))

    post_id = request.form.get('post_id')

    if not post_id:
        flash("Invalid unreact request.", "warning")
        return redirect(request.referrer or url_for('feed'))

    db = get_db()

    # Remove the reaction if it exists
    existing_reaction = query_db(
        'SELECT id FROM reactions WHERE post_id = ? AND user_id = ?',
        (post_id, user_id),
        one=True
    )

    if existing_reaction:
        db.execute('DELETE FROM reactions WHERE id = ?', (existing_reaction['id'],))
        db.commit()
        flash("Reaction removed.", "success")
    else:
        flash("No reaction to remove.", "info")

    return redirect(request.referrer or url_for('feed'))


@app.route('/u/<int:user_id>/follow', methods=['POST'])
def follow_user(user_id):
    """Handles the logic for the current user to follow another user."""
    follower_id = session.get('user_id')

    # Security: Ensure user is logged in
    if not follower_id:
        flash("You must be logged in to follow users.", "danger")
        return redirect(url_for('login'))

    # Security: Prevent users from following themselves
    if follower_id == user_id:
        flash("You cannot follow yourself.", "warning")
        return redirect(request.referrer or url_for('feed'))

    # Check if the user to be followed actually exists
    user_to_follow = query_db('SELECT id FROM users WHERE id = ?', (user_id,), one=True)
    if not user_to_follow:
        flash("The user you are trying to follow does not exist.", "danger")
        return redirect(request.referrer or url_for('feed'))
        
    db = get_db()
    try:
        # Insert the follow relationship. The PRIMARY KEY constraint will prevent duplicates if you've set one.
        db.execute('INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)',
                   (follower_id, user_id))
        db.commit()
        username_to_follow = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)['username']
        flash(f"You are now following {username_to_follow}.", "success")
    except sqlite3.IntegrityError:
        flash("You are already following this user.", "info")

    return redirect(request.referrer or url_for('feed'))


@app.route('/u/<int:user_id>/unfollow', methods=['POST'])
def unfollow_user(user_id):
    """Handles the logic for the current user to unfollow another user."""
    follower_id = session.get('user_id')

    # Security: Ensure user is logged in
    if not follower_id:
        flash("You must be logged in to unfollow users.", "danger")
        return redirect(url_for('login'))

    db = get_db()
    cur = db.execute('DELETE FROM follows WHERE follower_id = ? AND followed_id = ?',
               (follower_id, user_id))
    db.commit()

    if cur.rowcount > 0:
        # cur.rowcount tells us if a row was actually deleted
        username_unfollowed = query_db('SELECT username FROM users WHERE id = ?', (user_id,), one=True)['username']
        flash(f"You have unfollowed {username_unfollowed}.", "success")
    else:
        # This case handles if someone tries to unfollow a user they weren't following
        flash("You were not following this user.", "info")

    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('feed'))

@app.route('/admin')
def admin_dashboard():
    """Displays the admin dashboard with users, posts, and comments, sorted by risk."""

    if session.get('username') != 'admin':
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for('feed'))

    RISK_LEVELS = { "HIGH": 5, "MEDIUM": 3, "LOW": 1 }
    PAGE_SIZE = 50

    def get_risk_profile(score):
        if score >= RISK_LEVELS["HIGH"]:
            return "HIGH", 3
        elif score >= RISK_LEVELS["MEDIUM"]:
            return "MEDIUM", 2
        elif score >= RISK_LEVELS["LOW"]:
            return "LOW", 1
        return "NONE", 0

    # Get pagination and current tab parameters
    try:
        users_page = int(request.args.get('users_page', 1))
        posts_page = int(request.args.get('posts_page', 1))
        comments_page = int(request.args.get('comments_page', 1))
    except ValueError:
        users_page = 1
        posts_page = 1
        comments_page = 1
    
    current_tab = request.args.get('tab', 'users') # Default to 'users' tab

    users_offset = (users_page - 1) * PAGE_SIZE
    
    # First, get all users to calculate risk, then apply pagination in Python
    # It's more complex to do this efficiently in SQL if risk calc is Python-side
    all_users_raw = query_db('SELECT id, username, profile, created_at FROM users')
    all_users = []
    for user in all_users_raw:
        user_dict = dict(user)
        user_risk_score = user_risk_analysis(user_dict['id'])
        risk_label, risk_sort_key = get_risk_profile(user_risk_score)
        user_dict['risk_label'] = risk_label
        user_dict['risk_sort_key'] = risk_sort_key
        user_dict['risk_score'] = min(5.0, round(user_risk_score, 2))
        all_users.append(user_dict)

    all_users.sort(key=lambda x: x['risk_score'], reverse=True)
    total_users = len(all_users)
    users = all_users[users_offset : users_offset + PAGE_SIZE]
    total_users_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE

    # --- Posts Tab Data ---
    posts_offset = (posts_page - 1) * PAGE_SIZE
    total_posts_count = query_db('SELECT COUNT(*) as count FROM posts', one=True)['count']
    total_posts_pages = (total_posts_count + PAGE_SIZE - 1) // PAGE_SIZE

    posts_raw = query_db(f'''
        SELECT p.id, p.content, p.created_at, u.username, u.created_at as user_created_at
        FROM posts p JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC -- Order by ID for consistent pagination before risk sort
        LIMIT ? OFFSET ?
    ''', (PAGE_SIZE, posts_offset))
    posts = []
    for post in posts_raw:
        post_dict = dict(post)
        _, base_score = moderate_content(post_dict['content'])
        final_score = base_score 
        author_created_dt = post_dict['user_created_at']
        author_age_days = (datetime.utcnow() - author_created_dt).days
        if author_age_days < 7:
            final_score *= 1.5
        risk_label, risk_sort_key = get_risk_profile(final_score)
        post_dict['risk_label'] = risk_label
        post_dict['risk_sort_key'] = risk_sort_key
        post_dict['risk_score'] = round(final_score, 2)
        posts.append(post_dict)

    posts.sort(key=lambda x: x['risk_score'], reverse=True) # Sort after fetching and scoring

    # --- Comments Tab Data ---
    comments_offset = (comments_page - 1) * PAGE_SIZE
    total_comments_count = query_db('SELECT COUNT(*) as count FROM comments', one=True)['count']
    total_comments_pages = (total_comments_count + PAGE_SIZE - 1) // PAGE_SIZE

    comments_raw = query_db(f'''
        SELECT c.id, c.content, c.created_at, u.username, u.created_at as user_created_at
        FROM comments c JOIN users u ON c.user_id = u.id
        ORDER BY c.id DESC -- Order by ID for consistent pagination before risk sort
        LIMIT ? OFFSET ?
    ''', (PAGE_SIZE, comments_offset))
    comments = []
    for comment in comments_raw:
        comment_dict = dict(comment)
        _, score = moderate_content(comment_dict['content'])
        author_created_dt = comment_dict['user_created_at']
        author_age_days = (datetime.utcnow() - author_created_dt).days
        if author_age_days < 7:
            score *= 1.5
        risk_label, risk_sort_key = get_risk_profile(score)
        comment_dict['risk_label'] = risk_label
        comment_dict['risk_sort_key'] = risk_sort_key
        comment_dict['risk_score'] = round(score, 2)
        comments.append(comment_dict)

    comments.sort(key=lambda x: x['risk_score'], reverse=True) # Sort after fetching and scoring


    return render_template('admin.html.j2', 
                           users=users, 
                           posts=posts, 
                           comments=comments,
                           
                           # Pagination for Users
                           users_page=users_page,
                           total_users_pages=total_users_pages,
                           users_has_next=(users_page < total_users_pages),
                           users_has_prev=(users_page > 1),

                           # Pagination for Posts
                           posts_page=posts_page,
                           total_posts_pages=total_posts_pages,
                           posts_has_next=(posts_page < total_posts_pages),
                           posts_has_prev=(posts_page > 1),

                           # Pagination for Comments
                           comments_page=comments_page,
                           total_comments_pages=total_comments_pages,
                           comments_has_next=(comments_page < total_comments_pages),
                           comments_has_prev=(comments_page > 1),

                           current_tab=current_tab,
                           PAGE_SIZE=PAGE_SIZE)



@app.route('/admin/delete/user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))
        
    if user_id == session.get('user_id'):
        flash('You cannot delete your own account from the admin panel.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    flash(f'User {user_id} and all their content has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/post/<int:post_id>', methods=['POST'])
def admin_delete_post(post_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))

    db = get_db()
    db.execute('DELETE FROM comments WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM reactions WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()
    flash(f'Post {post_id} has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete/comment/<int:comment_id>', methods=['POST'])
def admin_delete_comment(comment_id):
    if session.get('username') != 'admin':
        flash("You do not have permission to perform this action.", "danger")
        return redirect(url_for('feed'))

    db = get_db()
    db.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()
    flash(f'Comment {comment_id} has been deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/rules')
def rules():
    return render_template('rules.html.j2')

@app.template_global()
def loop_color(user_id):
    # Generate a pastel color based on user_id hash
    h = hashlib.md5(str(user_id).encode()).hexdigest()
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return f'rgb({r % 128 + 80}, {g % 128 + 80}, {b % 128 + 80})'


# ----- Functions to be implemented are below
# Coding Assignment #2

# Assignment 2.2
def user_risk_analysis(user_id):
    user = query_db(
        "SELECT id, profile, created_at FROM users WHERE id = ?",
        (user_id,),
        one=True
    )

    if not user:
        return 0.0

    _, profile_score = moderate_content(user["profile"] or "")

    posts = query_db(
        "SELECT content FROM posts WHERE user_id = ?",
        (user_id,)
    )

    post_scores = [
        moderate_content(post["content"] or "")[1]
        for post in posts
    ]

    average_post_score = (
        sum(post_scores) / len(post_scores)
        if post_scores else 0.0
    )

    comments = query_db(
        "SELECT content FROM comments WHERE user_id = ?",
        (user_id,)
    )

    comment_scores = [
        moderate_content(comment["content"] or "")[1]
        for comment in comments
    ]

    average_comment_score = (
        sum(comment_scores) / len(comment_scores)
        if comment_scores else 0.0
    )

    content_risk_score = (
        profile_score
        + (average_post_score * 3)
        + average_comment_score
    )

    account_age_days = (
        datetime.utcnow() - user["created_at"]
    ).days

    if account_age_days < 7:
        content_risk_score *= 1.5
    elif account_age_days < 30:
        content_risk_score *= 1.2

    return min(5.0, content_risk_score)

    
# Assignment 2.1
def moderate_content(content):
    
    import re

    if not content:
        return content or "", 0.0

    moderated_content = content
    score = 0.0

    for word in TIER1_WORDS:
        pattern = r"\b" + re.escape(word) + r"\b"

        if re.search(pattern, content, flags=re.IGNORECASE):
            return (
                "[content removed due to severe violation]",
                5.0
            )

    for phrase in TIER2_PHRASES:
        pattern = re.escape(phrase)

        if re.search(pattern, content, flags=re.IGNORECASE):
            return (
                "[content removed due to spam/scam policy]",
                5.0
            )

    for word in TIER3_WORDS:
        pattern = r"\b" + re.escape(word) + r"\b"

        matches = list(re.finditer(
            pattern,
            moderated_content,
            flags=re.IGNORECASE
        ))

        for match in reversed(matches):
            replacement = "*" * len(match.group(0))

            moderated_content = (
                moderated_content[:match.start()]
                + replacement
                + moderated_content[match.end():]
            )

        score += 2.0 * len(matches)

    url_pattern = r"https?://[^\s]+|www\.[^\s]+"
    urls = list(re.finditer(url_pattern, moderated_content))

    for match in reversed(urls):
        moderated_content = (
            moderated_content[:match.start()]
            + "[link removed]"
            + moderated_content[match.end():]
        )

    score += 2.0 * len(urls)

    letters = [char for char in content if char.isalpha()]

    if len(letters) > 15:
        uppercase_count = sum(char.isupper() for char in letters)

        if uppercase_count / len(letters) > 0.70:
            score += 0.5

    return moderated_content, float(score)

# Coding Assignment #3
# Assignment 3.1
def recommend(user_id, filter_following):
    """
    Args:
        user_id: The ID of the current user.
        filter_following: Boolean, True if we only want to see recommendations from followed users.

    Returns:
        A list of 5 recommended posts, in reverse-chronological order.

    To test whether your recommendation algorithm works, let's pretend we like the DIY topic. Here are some users that often post DIY comment and a few example posts. Make sure your account did not engage with anything else. You should test your algorithm with these and see if your recommendation algorithm picks up on your interest in DIY and starts showing related content.
    
    Users: @starboy99, @DancingDolphin, @blogger_bob
    Posts: 1810, 1875, 1880, 2113
    
    Materials: 
    - https://www.nvidia.com/en-us/glossary/recommendation-system/
    - http://www.configworks.com/mz/handout_recsys_sac2010.pdf
    - https://www.researchgate.net/publication/227268858_Recommender_Systems_Handbook
    """

    RESULT_SIZE = 5
    CANDIDATE_POOL = 300        
    W_FOLLOWED_AUTHOR = 2.0     
    W_LIKED_AUTHOR = 0.5        
    W_FRIEND_LIKE = 0.5        
    W_POPULARITY = 0.05         
 
    popularity_join = """
        LEFT JOIN (SELECT post_id, COUNT(*) AS total
                   FROM reactions GROUP BY post_id) r ON p.id = r.post_id
    """
 
    if not user_id:
        rows = query_db(f"""
            SELECT p.id, p.content, p.created_at, u.username, u.id AS user_id,
                   IFNULL(r.total, 0) AS total_reactions
            FROM posts p
            JOIN users u ON p.user_id = u.id
            {popularity_join}
            ORDER BY total_reactions DESC, p.created_at DESC
            LIMIT ?
        """, (RESULT_SIZE,)) or []
        posts = [dict(row) for row in rows]
        posts.sort(key=lambda item: item['created_at'], reverse=True)
        return posts
 
    marks = ','.join('?' * len(POSITIVE_REACTIONS))
    liked_rows = query_db(f"""
        SELECT p.content, p.user_id
        FROM reactions r
        JOIN posts p ON r.post_id = p.id
        WHERE r.user_id = ? AND r.reaction_type IN ({marks})
    """, [user_id] + list(POSITIVE_REACTIONS)) or []
 
    topic_counts = collections.Counter()
    for row in liked_rows:
        topic_counts.update(_keywords(row['content']))
    max_count = max(topic_counts.values()) if topic_counts else 1
    topic_weight = {w: c / max_count for w, c in topic_counts.items()}
 
    liked_authors = collections.Counter(row['user_id'] for row in liked_rows)
 
    followed_ids = [
        row['followed_id'] for row in
        (query_db('SELECT followed_id FROM follows WHERE follower_id = ?',
                  (user_id,)) or [])
    ]
 
    friend_likes = {}
    if followed_ids:
        id_marks = ','.join('?' * len(followed_ids))
        rows = query_db(f"""
            SELECT post_id, COUNT(*) AS c
            FROM reactions
            WHERE user_id IN ({id_marks}) AND reaction_type IN ({marks})
            GROUP BY post_id
        """, followed_ids + list(POSITIVE_REACTIONS)) or []
        friend_likes = {row['post_id']: row['c'] for row in rows}
 
    follow_filter = ""
    args = [user_id, user_id, user_id]
    if filter_following:
        follow_filter = ("AND p.user_id IN "
                         "(SELECT followed_id FROM follows WHERE follower_id = ?)")
        args.append(user_id)
    args.append(CANDIDATE_POOL)
 
    candidates = query_db(f"""
        SELECT p.id, p.content, p.created_at, u.username, u.id AS user_id,
               IFNULL(r.total, 0) AS total_reactions
        FROM posts p
        JOIN users u ON p.user_id = u.id
        {popularity_join}
        WHERE p.user_id != ?
          AND p.id NOT IN (SELECT post_id FROM reactions WHERE user_id = ?)
          AND p.id NOT IN (SELECT post_id FROM comments  WHERE user_id = ?)
          {follow_filter}
        ORDER BY p.created_at DESC
        LIMIT ?
    """, args) or []
 
    scored = []
    for post in candidates:
        words = set(_keywords(post['content']))
 
        content_score = 0.0
        if words and topic_weight:
            content_score = (sum(topic_weight.get(w, 0.0) for w in words)
                             / math.sqrt(len(words)))
 
        score = content_score
        if post['user_id'] in followed_ids:
            score += W_FOLLOWED_AUTHOR
        score += W_LIKED_AUTHOR * min(liked_authors.get(post['user_id'], 0), 3)
        score += W_FRIEND_LIKE * min(friend_likes.get(post['id'], 0), 3)
        score += W_POPULARITY * min(post['total_reactions'], 10)
 
        post_dict = dict(post)
        post_dict['rec_score'] = round(score, 3)
        scored.append(post_dict)
 
    scored.sort(key=lambda item: (item['rec_score'], item['created_at']),
                reverse=True)
    top = scored[:RESULT_SIZE]
    top.sort(key=lambda item: item['created_at'], reverse=True)
    return top

def init_extra_tables():
    """Creates the table used by the sandbox feature if it is missing."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sandbox_posts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            content    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_extra_tables()

NEWCOMER_UNLOCK = 3
TRUSTED_UNLOCK = 10
TRUSTED_MIN_DAYS = 7
DAILY_POST_LIMIT = 5

ACCESS_LABELS = {0: 'Newcomer', 1: 'Contributor', 2: 'Trusted member'}


def _to_datetime(value):
    """SQLite may give us a datetime or a string; always return a datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
    return datetime.utcnow()


def get_access_level(user_id):
    """Returns a dict describing what the user is currently allowed to do."""
    if not user_id:
        return None

    user = query_db('SELECT username, created_at FROM users WHERE id = ?',
                    (user_id,), one=True)
    if not user:
        return None

    comments = query_db('SELECT COUNT(*) AS c FROM comments WHERE user_id = ?',
                        (user_id,), one=True)
    reactions = query_db('SELECT COUNT(*) AS c FROM reactions WHERE user_id = ?',
                         (user_id,), one=True)
    interactions = (comments['c'] if comments else 0) + \
                   (reactions['c'] if reactions else 0)

    age_days = (datetime.utcnow() - _to_datetime(user['created_at'])).days

    if user['username'] == 'admin' or (interactions >= TRUSTED_UNLOCK
                                       and age_days >= TRUSTED_MIN_DAYS):
        level = 2
    elif interactions >= NEWCOMER_UNLOCK:
        level = 1
    else:
        level = 0

    goal = NEWCOMER_UNLOCK if level == 0 else TRUSTED_UNLOCK
    remaining = max(0, goal - interactions)
    percentage = 100 if level == 2 else min(100, int(interactions / goal * 100))
    days_left = max(0, TRUSTED_MIN_DAYS - age_days)

    posts_today = 0
    if level == 1:
        since = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        row = query_db('SELECT COUNT(*) AS c FROM posts '
                       'WHERE user_id = ? AND created_at >= ?',
                       (user_id, since), one=True)
        posts_today = row['c'] if row else 0

    if level == 0:
        can_post = False
        reason = (f"Newcomers cannot publish posts yet. Make {remaining} more "
                  f"comment(s) or reaction(s) to unlock posting, and practise "
                  f"in the sandbox meanwhile.")
    elif level == 1 and posts_today >= DAILY_POST_LIMIT:
        can_post = False
        reason = (f"Contributors can publish {DAILY_POST_LIMIT} posts per 24 hours. "
                  f"Keep interacting to become a trusted member.")
    else:
        can_post = True
        reason = ''

    return {
        'level': level,
        'label': ACCESS_LABELS[level],
        'interactions': interactions,
        'goal': goal,
        'remaining': remaining,
        'percentage': percentage,
        'days_left': days_left,
        'can_post': can_post,
        'posts_today': posts_today,
        'daily_limit': DAILY_POST_LIMIT if level == 1 else None,
        'reason': reason,
    }


@app.context_processor
def inject_access_info():
    """Makes `access_info` available in EVERY template (feed, profile, ...)."""
    return {'access_info': get_access_level(session.get('user_id'))}


SANDBOX_TTL_HOURS = 12
SANDBOX_MAX_POSTS = 20


@app.route('/sandbox', methods=['GET', 'POST'])
def sandbox():
    user_id = session.get('user_id')
    if not user_id:
        flash('You must be logged in to use the sandbox.', 'danger')
        return redirect(url_for('login'))

    cutoff = (datetime.utcnow() - timedelta(hours=SANDBOX_TTL_HOURS)) \
        .strftime('%Y-%m-%d %H:%M:%S')
    query_db('DELETE FROM sandbox_posts WHERE created_at < ?',
             (cutoff,), commit=True)

    if request.method == 'POST':
        content = (request.form.get('content') or '').strip()
        count = query_db('SELECT COUNT(*) AS c FROM sandbox_posts WHERE user_id = ?',
                         (user_id,), one=True)['c']
        if not content:
            flash('Write something to try out first.', 'warning')
        elif len(content) > 500:
            flash('Practice posts are limited to 500 characters.', 'warning')
        elif count >= SANDBOX_MAX_POSTS:
            flash('Your sandbox is full - clear it to keep practising.', 'warning')
        else:
            query_db('INSERT INTO sandbox_posts (user_id, content) VALUES (?, ?)',
                     (user_id, content), commit=True)
            flash('Practice post saved. Only you can see it.', 'success')
        return redirect(url_for('sandbox'))

    rows = query_db('SELECT id, content, created_at FROM sandbox_posts '
                    'WHERE user_id = ? ORDER BY id DESC', (user_id,)) or []

    practice_posts = []
    for row in rows:
        preview, score = moderate_content(row['content'])
        if score >= 5:
            risk = 'HIGH'
        elif score >= 3:
            risk = 'MEDIUM'
        elif score >= 1:
            risk = 'LOW'
        else:
            risk = 'NONE'
        practice_posts.append({
            'id': row['id'],
            'original': row['content'],
            'preview': preview,
            'changed': preview != row['content'],
            'score': score,
            'risk': risk,
            'created_at': row['created_at'],
        })

    return render_template('sandbox.html.j2',
                           practice_posts=practice_posts,
                           ttl_hours=SANDBOX_TTL_HOURS,
                           max_posts=SANDBOX_MAX_POSTS)


@app.route('/sandbox/clear', methods=['POST'])
def sandbox_clear():
    """Lets the user wipe their own practice posts."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    query_db('DELETE FROM sandbox_posts WHERE user_id = ?', (user_id,), commit=True)
    flash('Sandbox cleared.', 'info')
    return redirect(url_for('sandbox'))


if __name__ == '__main__':
    app.run(debug=True, port=8080)

