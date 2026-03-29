from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from datetime import datetime
import os
import logging

# Configurar logging para melhor debug
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuração otimizada para produção
# Como deve ficar
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='eventlet',  # <--- MUDANÇA AQUI
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25
)


# Armazenar dados
usuarios = {}
mensagens_offline = {}

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'versao': '2.0',
        'usuarios_online': len(usuarios),
        'lista_usuarios': list(usuarios.keys()),
        'mensagens_pendentes': sum(len(msgs) for msgs in mensagens_offline.values())
    })

@app.route('/status')
def status():
    return jsonify({
        'status': 'online',
        'usuarios': list(usuarios.keys()),
        'total_usuarios': len(usuarios),
        'offline_messages': {u: len(msgs) for u, msgs in mensagens_offline.items()},
        'total_mensagens_pendentes': sum(len(msgs) for msgs in mensagens_offline.values())
    })

@app.route('/health')
def health():
    """Endpoint para health check do Render"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@socketio.on('connect')
def handle_connect():
    logger.info(f'[CONNECT] Cliente conectado: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    username_to_remove = None
    for username, data in usuarios.items():
        if data.get('sid') == request.sid:
            username_to_remove = username
            break
    
    if username_to_remove:
        del usuarios[username_to_remove]
        logger.info(f'[DISCONNECT] Usuario desconectado: {username_to_remove}')
        logger.info(f'[INFO] Total online: {len(usuarios)}')
        
        lista = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
        emit('lista_usuarios', lista, broadcast=True)

@socketio.on('registrar_usuario')
def handle_registrar_usuario(data):
    logger.info(f'[REGISTRO] Recebido de {request.sid}: {data.get("username")}')
    
    username = data.get('username')
    public_key = data.get('public_key')
    
    if not username or not public_key:
        logger.warning(f'[ERRO] Dados incompletos: username={username}, has_key={bool(public_key)}')
        emit('error', {'message': 'Dados incompletos para registro'}, room=request.sid)
        return
    
    # Verificar se usuário já está online em outra conexão
    if username in usuarios and usuarios[username].get('sid') != request.sid:
        logger.warning(f'[AVISO] Usuario {username} ja conectado em outro dispositivo')
        # Remove conexão anterior
        old_sid = usuarios[username]['sid']
        emit('force_disconnect', {'message': 'Conectado em outro dispositivo'}, room=old_sid)
    
    usuarios[username] = {
        'sid': request.sid,
        'public_key': public_key,
        'conectado_em': datetime.now().isoformat()
    }
    
    logger.info(f'[OK] Usuario registrado: {username}')
    logger.info(f'[INFO] Total online: {len(usuarios)}')
    
    # Envia lista atualizada para TODOS
    lista_usuarios = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
    emit('lista_usuarios', lista_usuarios, broadcast=True)
    logger.info(f'[INFO] Lista enviada para todos: {len(lista_usuarios)} usuarios')
    
    # Envia chave do novo usuário para todos
    emit('chave_usuario', {'username': username, 'public_key': public_key}, broadcast=True, include_self=False)
    
    # Verifica mensagens offline
    if username in mensagens_offline and mensagens_offline[username]:
        qtd = len(mensagens_offline[username])
        logger.info(f'[OFFLINE] Entregando {qtd} mensagens para {username}')
        for msg in mensagens_offline[username]:
            emit('message', {
                'from': msg['from'],
                'content': msg['content'],
                'offline': True,
                'timestamp': msg.get('timestamp', datetime.now().isoformat())
            }, room=request.sid)
            logger.info(f'[ENTREGA] Mensagem de {msg["from"]} entregue')
        del mensagens_offline[username]

@socketio.on('solicitar_usuarios')
def handle_solicitar_usuarios():
    lista_usuarios = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
    emit('lista_usuarios', lista_usuarios)
    logger.info(f'[SOLICITAR] Lista enviada para {request.sid}: {len(lista_usuarios)} usuarios')

@socketio.on('enviar_chave')
def handle_key(data):
    logger.info(f'[KEY] Chave recebida de {request.sid}')
    emit('receber_chave', data, broadcast=True, include_self=False)

@socketio.on('message')
def handle_message(data):
    logger.info(f'[MENSAGEM] Recebida de {request.sid}: {data.get("from")} -> {data.get("to")}')
    
    if isinstance(data, dict) and 'to' in data:
        to = data.get('to')
        from_user = data.get('from')
        content = data.get('content')
        
        if not to or not from_user or not content:
            logger.warning(f'[ERRO] Dados incompletos: to={to}, from={from_user}, has_content={bool(content)}')
            emit('error', {'message': 'Dados da mensagem incompletos'}, room=request.sid)
            return
        
        destinatario = usuarios.get(to)
        
        if destinatario:
            # Destinatário ONLINE - entrega imediata
            logger.info(f'[ENTREGA] {from_user} -> {to} (ONLINE)')
            try:
                emit('message', {
                    'from': from_user,
                    'content': content,
                    'offline': False,
                    'timestamp': datetime.now().isoformat()
                }, room=destinatario['sid'])
                
                emit('delivery_confirmation', {
                    'to': to,
                    'from': from_user,
                    'status': 'delivered',
                    'timestamp': datetime.now().isoformat()
                }, room=request.sid)
                logger.info(f'[OK] Mensagem entregue para {to}')
            except Exception as e:
                logger.error(f'[ERRO] Falha ao entregar mensagem: {e}')
                emit('delivery_confirmation', {
                    'to': to,
                    'from': from_user,
                    'status': 'failed',
                    'error': str(e)
                }, room=request.sid)
        else:
            # Destinatário OFFLINE - armazena mensagem
            logger.info(f'[OFFLINE] Armazenando msg de {from_user} para {to}')
            if to not in mensagens_offline:
                mensagens_offline[to] = []
            
            mensagens_offline[to].append({
                'from': from_user,
                'content': content,
                'timestamp': datetime.now().isoformat()
            })
            
            qtd_pendentes = len(mensagens_offline[to])
            logger.info(f'[ARMAZENADA] {to} tem {qtd_pendentes} mensagem(ns) pendente(s)')
            
            emit('delivery_confirmation', {
                'to': to,
                'from': from_user,
                'status': 'stored_offline',
                'timestamp': datetime.now().isoformat()
            }, room=request.sid)
    else:
        # Formato antigo - broadcast
        logger.info(f'[BROADCAST] Repassando mensagem no formato antigo')
        emit('message', data, broadcast=True, include_self=False)

# Health check para o Render
@app.route('/healthz')
def healthz():
    return 'OK', 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print('=' * 60)
    print('SERVIDOR CHAT - RENDER.COM (MODO THREADING)')
    print('=' * 60)
    print(f'[INFO] Servidor rodando na porta {port}')
    print(f'[INFO] Health check: http://0.0.0.0:{port}/health')
    print(f'[INFO] Status: http://0.0.0.0:{port}/status')
    print('=' * 60)
    
    # Apenas UM socketio.run (a linha duplicada foi removida)
    socketio.run(
        app, 
        host='0.0.0.0', 
        port=port, 
        debug=False,
        use_reloader=False
    )
