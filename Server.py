import eventlet
eventlet.monkey_patch()

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from datetime import datetime
import os
import logging
import psycopg2
from psycopg2 import IntegrityError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25
)

# ==================== CONEXÃO COM NEON (POSTGRESQL) ====================
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurada")
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(database_url)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            public_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS mensagens (
            id SERIAL PRIMARY KEY,
            de TEXT NOT NULL,
            para TEXT NOT NULL,
            conteudo TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            lida BOOLEAN DEFAULT FALSE,
            entregue BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (de) REFERENCES usuarios(username),
            FOREIGN KEY (para) REFERENCES usuarios(username)
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Banco de dados inicializado.")

init_db()

# ==================== MEMÓRIA PARA SESSÕES ONLINE ====================
usuarios_online = {}
sid_to_username = {}
mensagens_offline = {}

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'versao': '3.0',
        'usuarios_online': len(usuarios_online),
        'lista_usuarios': list(usuarios_online.keys()),
        'mensagens_pendentes': sum(len(msgs) for msgs in mensagens_offline.values())
    })

@app.route('/status')
def status():
    return jsonify({
        'status': 'online',
        'usuarios_online': list(usuarios_online.keys()),
        'total_usuarios_cadastrados': obter_total_usuarios()
    })

def obter_total_usuarios():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM usuarios')
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

# ==================== EVENTOS SOCKET.IO ====================
@socketio.on('connect')
def handle_connect():
    logger.info(f'[CONNECT] Cliente conectado: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    username = sid_to_username.get(request.sid)
    if username:
        del usuarios_online[username]
        del sid_to_username[request.sid]
        logger.info(f'[DISCONNECT] Usuario {username} desconectado')

@socketio.on('registrar_usuario')
def handle_registrar_usuario(data):
    username = data.get('username')
    public_key = data.get('public_key')
    if not username or not public_key:
        logger.warning(f'[ERRO] Dados incompletos: {data}')
        return

    # Atualiza a chave pública no banco (se o usuário já existir)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE usuarios SET public_key = %s WHERE username = %s', (public_key, username))
    if cur.rowcount == 0:
        logger.warning(f'Usuário {username} não encontrado no banco para atualizar chave')
        conn.rollback()
    else:
        conn.commit()
    cur.close()
    conn.close()

    # Adiciona à lista de online
    usuarios_online[username] = {'sid': request.sid, 'public_key': public_key}
    sid_to_username[request.sid] = username
    logger.info(f'[ONLINE] Usuario {username} registrado')

    # Entrega mensagens offline (se houver)
    if username in mensagens_offline:
        for msg in mensagens_offline[username]:
            emit('message', msg, room=request.sid)
        del mensagens_offline[username]

@socketio.on('solicitar_contatos')
def handle_solicitar_contatos():
    username_atual = sid_to_username.get(request.sid)
    if not username_atual:
        logger.warning('[SOLICITAR] Usuário não identificado')
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT username, public_key FROM usuarios')
    todos = cur.fetchall()
    cur.close()
    conn.close()
    contatos = []
    for user, pub_key in todos:
        if user == username_atual:
            continue
        online = user in usuarios_online
        contato = {'username': user, 'online': online}
        if online:
            contato['public_key'] = usuarios_online[user]['public_key']
        else:
            contato['public_key'] = pub_key
        contatos.append(contato)
    emit('lista_contatos', contatos, room=request.sid)
    logger.info(f'[CONTATOS] {len(contatos)} enviados para {username_atual}')

@socketio.on('message')
def handle_message(data):
    de = data.get('from')
    para = data.get('to')
    conteudo = data.get('content')
    if not de or not para or not conteudo:
        emit('error', {'message': 'Dados incompletos'}, room=request.sid)
        return

    # Salvar no banco
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO mensagens (de, para, conteudo, timestamp) VALUES (%s, %s, %s, %s)',
                    (de, para, conteudo, datetime.now()))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f'Erro ao salvar mensagem: {e}')

    destinatario = usuarios_online.get(para)
    if destinatario:
        emit('message', {
            'from': de,
            'content': conteudo,
            'offline': False,
            'timestamp': datetime.now().isoformat()
        }, room=destinatario['sid'])
        emit('delivery_confirmation', {'to': para, 'from': de, 'status': 'delivered'}, room=request.sid)
    else:
        if para not in mensagens_offline:
            mensagens_offline[para] = []
        mensagens_offline[para].append({
            'from': de,
            'content': conteudo,
            'offline': True,
            'timestamp': datetime.now().isoformat()
        })
        emit('delivery_confirmation', {'to': para, 'from': de, 'status': 'stored_offline'}, room=request.sid)

@socketio.on('digitando')
def handle_digitando(data):
    to = data.get('to')
    from_user = data.get('from')
    if to and from_user:
        destinatario = usuarios_online.get(to)
        if destinatario:
            emit('digitando', {'from': from_user}, room=destinatario['sid'])

@socketio.on('solicitar_historico')
def handle_solicitar_historico(data):
    usuario = sid_to_username.get(request.sid)
    contato = data.get('contato')
    if not usuario or not contato:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT de, conteudo, timestamp, lida, entregue
        FROM mensagens
        WHERE (de = %s AND para = %s) OR (de = %s AND para = %s)
        ORDER BY timestamp ASC
        LIMIT 100
    ''', (usuario, contato, contato, usuario))
    mensagens = cur.fetchall()
    cur.close()
    conn.close()
    historico = []
    for de, conteudo, ts, lida, entregue in mensagens:
        historico.append({
            'from': de,
            'content': conteudo,
            'timestamp': ts.isoformat(),
            'lida': lida,
            'entregue': entregue
        })
    emit('historico_mensagens', {'contato': contato, 'mensagens': historico}, room=request.sid)

@socketio.on('marcar_lida')
def handle_marcar_lida(data):
    usuario = sid_to_username.get(request.sid)
    contato = data.get('contato')
    if not usuario or not contato:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        UPDATE mensagens
        SET lida = TRUE
        WHERE para = %s AND de = %s AND lida = FALSE
    ''', (usuario, contato))
    conn.commit()
    cur.close()
    conn.close()

@socketio.on('registrar_usuario_credencial')
def handle_registro_credencial(data):
    username = data.get('username')
    password_hash = data.get('password_hash')
    if not username or not password_hash:
        emit('registro_response', {'success': False, 'message': 'Dados incompletos'}, room=request.sid)
        return
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO usuarios (username, password_hash) VALUES (%s, %s)', (username, password_hash))
        conn.commit()
        emit('registro_response', {'success': True, 'message': 'Usuário criado'}, room=request.sid)
    except IntegrityError:
        conn.rollback()
        emit('registro_response', {'success': False, 'message': 'Usuário já existe'}, room=request.sid)
    finally:
        cur.close()
        conn.close()

@socketio.on('login_usuario')
def handle_login_credencial(data):
    username = data.get('username')
    password_hash = data.get('password_hash')
    if not username or not password_hash:
        emit('login_response', {'success': False, 'message': 'Dados incompletos'}, room=request.sid)
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT password_hash FROM usuarios WHERE username = %s', (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row and row[0] == password_hash:
        emit('login_response', {'success': True, 'username': username, 'message': 'OK'}, room=request.sid)
    else:
        emit('login_response', {'success': False, 'message': 'Usuário ou senha incorretos'}, room=request.sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print('=' * 60)
    print('SERVIDOR CHAT - NEON (CORRIGIDO)')
    print('=' * 60)
    print(f'[INFO] Servidor rodando na porta {port}')
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
