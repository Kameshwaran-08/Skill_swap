"""
SkillSync — Simple Demo Version
"""

import eventlet
eventlet.monkey_patch()

import os, datetime, bcrypt
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from supabase import create_client, Client

# ── CONFIG ──────────────────────────────────────────────────────────
SUPABASE_URL = "https://txruejsmhhfqjzylbkll.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR4cnVlanNtaGhmcWp6eWxia2xsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIyNTY0OTgsImV4cCI6MjA4NzgzMjQ5OH0.CqQZFdd-xAhxC8Q6JEWB_O8h_bkdX01NrtDA5YWmAys"

app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'skillsync_demo_2025'

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
)

db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

sid_map: dict = {}


def _room_name(u1, u2):
    return '_'.join(sorted([u1, u2]))


def _compat(a, b):
    a_off  = set(a.get('offered', []))
    a_want = set(a.get('wanted',  []))
    b_off  = set(b.get('offered', []))
    b_want = set(b.get('wanted',  []))
    shared = total = 0
    for s in a_want:
        total += 1
        if s in b_off: shared += 1
    for s in b_want:
        total += 1
        if s in a_off: shared += 1
    skill = (shared / total * 100) if total else 0
    a_av  = set(a.get('availability', []))
    b_av  = set(b.get('availability', []))
    avail = len(a_av & b_av) / max(len(a_av), len(b_av)) * 100 if a_av and b_av else 50
    rep   = min((b.get('reputation', 10) / 200) * 100, 100)
    return round(skill * 0.5 + avail * 0.3 + rep * 0.2)


def _is_online(username):
    return username in sid_map and sid_map[username] is not None


def _get_all_online_users():
    online_names = [u for u, sid in sid_map.items() if sid is not None]
    if not online_names:
        return []
    res = db.table('users').select(
        'username,name,offered,wanted,availability,reputation,is_online'
    ).in_('username', online_names).execute()
    return res.data or []


def _broadcast_users():
    online = _get_all_online_users()
    socketio.emit('update_user_list', {
        'users': online, 'node_count': len(online), 'edge_count': 0,
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })


def _mark_online(username, online):
    db.table('users').update({
        'is_online': online,
        'last_seen': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }).eq('username', username).execute()


@app.route('/')
@app.route('/dashboard')
@app.route('/dashboard.html')
def serve_index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok'}), 200


@app.route('/api/signup', methods=['POST'])
def api_signup():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    name     = (data.get('name')     or username).strip()
    email    = (data.get('email')    or '').strip().lower()
    password = (data.get('password') or '').strip()
    offered  = [s.strip().lower() for s in data.get('offered',  []) if s.strip()]
    wanted   = [s.strip().lower() for s in data.get('wanted',   []) if s.strip()]
    avail    = data.get('availability', [])

    if not username or not email or not password:
        return jsonify({'error': 'username, email and password are required'}), 400
    if not offered or not wanted:
        return jsonify({'error': 'offered and wanted skills are required'}), 400
    if db.table('users').select('username').eq('username', username).execute().data:
        return jsonify({'error': f"Username '{username}' is already taken"}), 409
    if db.table('users').select('username').eq('email', email).execute().data:
        return jsonify({'error': 'Email already registered'}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.table('users').insert({
        'username': username, 'name': name, 'email': email,
        'password_hash': pw_hash, 'offered': offered, 'wanted': wanted,
        'availability': avail, 'reputation': 10, 'is_online': False,
    }).execute()
    return jsonify({'ok': True, 'username': username, 'name': name})


@app.route('/api/login', methods=['POST'])
def api_login():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    password = (data.get('password') or '').strip()
    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400
    res = db.table('users').select('*').eq('username', username).execute()
    if not res.data:
        return jsonify({'error': 'Invalid username or password'}), 401
    user = res.data[0]
    if user.get('password_hash') == 'bot_no_login':
        return jsonify({'error': 'Invalid username or password'}), 401
    if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        return jsonify({'error': 'Invalid username or password'}), 401
    user.pop('password_hash', None)
    return jsonify({'ok': True, 'user': user})


@app.route('/api/get-matches/<username>')
def get_matches(username):
    res     = db.rpc('get_user_matches', {'p_username': username}).execute()
    matches = res.data or []
    if matches:
        partner_names = [m['partner'] for m in matches]
        profiles      = {p['username']: p for p in (
            db.table('users').select('username,name,offered,wanted,availability,reputation')
            .in_('username', partner_names).execute().data or []
        )}
        for m in matches:
            m['online']       = _is_online(m['partner'])
            p = profiles.get(m['partner'], {})
            m['partner_name'] = p.get('name', m['partner'])
            m['offered']      = p.get('offered', [])
            m['wanted']       = p.get('wanted', [])
            m['availability'] = p.get('availability', [])
            m['reputation']   = p.get('reputation', 10)
    return jsonify({'username': username, 'matches': matches})


@app.route('/api/messages/<room>')
def get_messages(room):
    res = db.table('messages').select('id,sender,content,created_at') \
            .eq('room', room).order('created_at', desc=False).limit(100).execute()
    return jsonify({'room': room, 'messages': res.data or []})


@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    offered  = [s.strip().lower() for s in data.get('offered',  []) if s.strip()]
    wanted   = [s.strip().lower() for s in data.get('wanted',   []) if s.strip()]
    avail    = data.get('availability', [])
    if not username:
        return jsonify({'error': 'username required'}), 400
    if not offered or not wanted:
        return jsonify({'error': 'offered and wanted skills are required'}), 400
    db.table('users').update({
        'offered': offered, 'wanted': wanted, 'availability': avail,
    }).eq('username', username).execute()
    return jsonify({'ok': True})


@app.route('/api/delete-account', methods=['POST'])
def delete_account():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    if not username:
        return jsonify({'error': 'username required'}), 400
    db.table('messages').delete().eq('sender', username).execute()
    try:
        db.table('connection_requests').delete().or_(
            f'from_user.eq.{username},to_user.eq.{username}'
        ).execute()
    except: pass
    try:
        db.rpc('delete_user_matches', {'p_username': username}).execute()
    except:
        try:
            db.table('matches').delete().or_(
                f'user_a.eq.{username},user_b.eq.{username}'
            ).execute()
        except: pass
    db.table('users').delete().eq('username', username).execute()
    if username in sid_map:
        del sid_map[username]
    return jsonify({'ok': True})


@socketio.on('connect')
def on_connect():
    pass


@socketio.on('register_user')
def on_register(data):
    username = (data.get('username') or '').strip()
    if not username:
        emit('registration_error', {'message': 'Username required.'}); return
    if username in sid_map and sid_map[username] not in (None, request.sid):
        emit('registration_error', {'message': f"'{username}' already connected."}); return

    sid_map[username] = request.sid
    join_room(username)
    _mark_online(username, True)

    me_res = db.table('users').select('*').eq('username', username).execute()
    if not me_res.data:
        emit('registration_error', {'message': 'User not found.'}); return
    me = me_res.data[0]

    others = db.table('users').select(
        'username,name,offered,wanted,availability,reputation'
    ).neq('username', username).execute().data or []

    new_matches = []
    for other in others:
        a_off  = set(me.get('offered',    []))
        a_want = set(me.get('wanted',     []))
        b_off  = set(other.get('offered', []))
        b_want = set(other.get('wanted',  []))
        if not ((a_off & b_want) and (b_off & a_want)):
            continue
        room      = _room_name(username, other['username'])
        compat_ab = _compat(me, other)
        compat_ba = _compat(other, me)
        try:
            db.rpc('upsert_match', {
                'p_user_a': username, 'p_user_b': other['username'],
                'p_room': room, 'p_compat_a': compat_ab, 'p_compat_b': compat_ba,
            }).execute()
        except: pass
        emit('match_found', {
            'room': room, 'partner': other['username'],
            'partner_name': other.get('name', other['username']),
            'compat': compat_ab, 'online': _is_online(other['username']),
            'message': f"Matched with {other.get('name', other['username'])}!",
        })
        if _is_online(other['username']):
            socketio.emit('match_found', {
                'room': room, 'partner': username,
                'partner_name': me.get('name', username),
                'compat': compat_ba, 'online': True,
                'message': f"Matched with {me.get('name', username)}!",
            }, room=other['username'])
        new_matches.append(other['username'])

    _broadcast_users()
    online_users = _get_all_online_users()
    emit('registration_success', {
        'message':     f"Connected! Welcome, {me.get('name', username)}.",
        'new_matches': new_matches,
        'users':       online_users,
        'node_count':  len(online_users),
        'edge_count':  0,
    })


@socketio.on('join_chat')
def on_join_chat(data):
    room = (data.get('room') or '').strip()
    if room: join_room(room)


@socketio.on('send_message')
def on_message(data):
    room     = (data.get('room')     or '').strip()
    username = (data.get('username') or '').strip()
    message  = (data.get('message')  or '').strip()
    if not room or not username or not message: return
    db.table('messages').insert({
        'room': room, 'sender': username, 'content': message[:500],
    }).execute()
    u_res = db.table('users').select('name').eq('username', username).execute()
    name  = u_res.data[0]['name'] if u_res.data else username
    socketio.emit('receive_message', {
        'username': username, 'name': name, 'message': message,
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, room=room, skip_sid=request.sid)


@socketio.on('typing')
def on_typing(data):
    room     = (data.get('room')     or '').strip()
    username = (data.get('username') or '').strip()
    if room and username:
        socketio.emit('typing', {'username': username}, room=room, skip_sid=request.sid)


@socketio.on('connection_request')
def on_conn_request(data):
    to      = (data.get('to')      or '').strip()
    from_   = (data.get('from')    or '').strip()
    message = (data.get('message') or '').strip()
    if not to or not from_: return
    if not _is_online(to):
        emit('peer_offline', {'username': to}); return
    try:
        db.table('connection_requests').insert({
            'from_user': from_, 'to_user': to,
            'message': message, 'status': 'pending'
        }).execute()
    except: pass
    u_res = db.table('users').select('name').eq('username', from_).execute()
    socketio.emit('connection_request', {
        'from': from_,
        'from_name': u_res.data[0]['name'] if u_res.data else from_,
        'message': message,
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, room=to)


@socketio.on('respond_request')
def on_respond(data):
    from_  = (data.get('from') or '').strip()
    to     = (data.get('to')   or '').strip()
    accept = bool(data.get('accept', False))
    if not from_ or not to: return
    try:
        db.table('connection_requests').update({
            'status': 'accepted' if accept else 'declined'
        }).eq('from_user', from_).eq('to_user', to).eq('status', 'pending').execute()
    except: pass
    if accept:
        room  = _room_name(from_, to)
        a_res = db.table('users').select('*').eq('username', from_).execute()
        b_res = db.table('users').select('*').eq('username', to).execute()
        if a_res.data and b_res.data:
            try:
                db.rpc('upsert_match', {
                    'p_user_a': from_, 'p_user_b': to, 'p_room': room,
                    'p_compat_a': _compat(a_res.data[0], b_res.data[0]),
                    'p_compat_b': _compat(b_res.data[0], a_res.data[0]),
                }).execute()
            except: pass
        to_res = db.table('users').select('name').eq('username', to).execute()
        socketio.emit('request_accepted', {
            'from': to,
            'from_name': to_res.data[0]['name'] if to_res.data else to,
            'room': room,
            'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, room=from_)
    else:
        to_res = db.table('users').select('name').eq('username', to).execute()
        socketio.emit('request_declined', {
            'from': to,
            'from_name': to_res.data[0]['name'] if to_res.data else to,
        }, room=from_)


@socketio.on('update_reputation')
def on_update_reputation(data):
    username   = (data.get('username') or '').strip()
    reputation = int(data.get('reputation', 10))
    db.table('users').update({'reputation': reputation}).eq('username', username).execute()
    _broadcast_users()


@socketio.on('disconnect')
def on_disconnect():
    dead = [u for u, sid in sid_map.items() if sid == request.sid and sid is not None]
    for u in dead:
        sid_map[u] = None
        _mark_online(u, False)
    if dead:
        _broadcast_users()


if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    print("=" * 50)
    print(f"  SkillSync  ->  http://localhost:{port}")
    print("=" * 50)
    socketio.run(app, host='0.0.0.0', port=port, debug=debug, use_reloader=False)
