import eventlet
eventlet.monkey_patch()
# O monkey_patch() transforma as funções padrão do Python (como as de rede e travas/RLocks) em versões "amigáveis" ao Eventlet. 
#Se você importar o Flask antes disso, o Python carrega as funções originais e elas entram em conflito, gerando o erro de "RLock(s) were not greened".

from flask import Flask
from flask_socketio import SocketIO, emit

app = Flask(__name__)
# O SocketIO gerencia as conexões em tempo real
socketio = SocketIO(app, cors_allowed_origins="*")

@socketio.on('enviar_chave')
def handle_key(data):
    # Repassa sua chave pública para os outros usuários
    emit('receber_chave', data, broadcast=True, include_self=False)

@socketio.on('message')
def handle_message(data):
    # Repassa a mensagem criptografada (bytes) para todos
    emit('message', data, broadcast=True, include_self=False)

if __name__ == '__main__':
    # O Render define a porta automaticamente, mas 5000 é o padrão local
    socketio.run(app, host='0.0.0.0', port=5000)
