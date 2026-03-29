from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from datetime import datetime
import os

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Armazenar dados
usuarios = {}
mensagens_offline = {}

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'usuarios_online': len(usuarios),
        'lista_usuarios': list(usuarios.keys())
    })

@app.route('/status')
def status():
    return jsonify({
        'usuarios': list(usuarios.keys()),
        'offline_messages': {u: len(msgs) for u, msgs in mensagens_offline.items()}
    })

@socketio.on('connect')
def handle_connect():
    print(f'[CONNECT] Cliente conectado: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    username_to_remove = None
    for username, data in usuarios.items():
        if data.get('sid') == request.sid:
            username_to_remove = username
            break
    
    if username_to_remove:
        del usuarios[username_to_remove]
        print(f'[DISCONNECT] Usuario desconectado: {username_to_remove}')
        
        lista = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
        emit('lista_usuarios', lista, broadcast=True)

@socketio.on('registrar_usuario')
def handle_registrar_usuario(data):
    print(f'[REGISTRO] Recebido: {data}')
    
    username = data.get('username')
    public_key = data.get('public_key')
    
    if username and public_key:
        usuarios[username] = {
            'sid': request.sid,
            'public_key': public_key
        }
        
        print(f'[OK] Usuario registrado: {username}')
        print(f'[INFO] Total online: {len(usuarios)}')
        
        lista_usuarios = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
        emit('lista_usuarios', lista_usuarios, broadcast=True)
        emit('chave_usuario', {'username': username, 'public_key': public_key}, broadcast=True, include_self=False)
        
        if username in mensagens_offline and mensagens_offline[username]:
            qtd = len(mensagens_offline[username])
            print(f'[OFFLINE] Entregando {qtd} mensagens para {username}')
            for msg in mensagens_offline[username]:
                emit('message', {
                    'from': msg['from'],
                    'content': msg['content'],
                    'offline': True
                }, room=request.sid)
            del mensagens_offline[username]

@socketio.on('solicitar_usuarios')
def handle_solicitar_usuarios():
    lista_usuarios = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
    emit('lista_usuarios', lista_usuarios)

@socketio.on('enviar_chave')
def handle_key(data):
    emit('receber_chave', data, broadcast=True, include_self=False)

@socketio.on('message')
def handle_message(data):
    print(f'[MENSAGEM] Recebida: {data}')
    
    if isinstance(data, dict) and 'to' in data:
        to = data.get('to')
        from_user = data.get('from')
        content = data.get('content')
        
        if to and from_user and content:
            destinatario = usuarios.get(to)
            
            if destinatario:
                print(f'[ENTREGA] {from_user} -> {to} (ONLINE)')
                emit('message', {'from': from_user, 'content': content, 'offline': False}, room=destinatario['sid'])
                emit('delivery_confirmation', {'to': to, 'from': from_user, 'status': 'delivered'}, room=request.sid)
            else:
                print(f'[OFFLINE] Armazenando msg de {from_user} para {to}')
                if to not in mensagens_offline:
                    mensagens_offline[to] = []
                mensagens_offline[to].append({'from': from_user, 'content': content, 'timestamp': str(datetime.now())})
                emit('delivery_confirmation', {'to': to, 'from': from_user, 'status': 'stored_offline'}, room=request.sid)
    else:
        emit('message', data, broadcast=True, include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print('=' * 60)
    print('SERVIDOR CHAT - RENDER.COM (MODO THREADING)')
    print('=' * 60)
    print(f'[INFO] Servidor rodando na porta {port}')
    print('=' * 60)
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
