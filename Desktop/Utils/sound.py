import io, os, sys, shutil, subprocess, threading, wave
import numpy as np
from functools import lru_cache
from Utils.variables import DATA_DIR


RATE    = 44100
VOLUME  = 0.5
PLAYERS = ('afplay', 'paplay', 'pw-play', 'aplay')    # mac, pulseaudio, pipewire, alsa; no windows toca pelo winsound e sem player cai no sino do tk

# (frequencia Hz, duracao s): arpejo ascendente de do maior quando o preco cai, dois graves descendentes quando sobe
NOTES = {
    'good': [(1046.5, 0.11), (1318.5, 0.11), (1568.0, 0.20)],
    'bad':  [(440.0, 0.20), (311.1, 0.34)],
}


# WAV MONO 16 BITS DO ALERTA: SENOIDE COM TERCEIRO HARMONICO, ATAQUE DE 5 MS E DECAIMENTO PARA NAO ESTALAR ENTRE NOTAS
@lru_cache
def getTone(kind):
    parts = []

    for f, duration in NOTES[kind]:
        t = np.arange(int(RATE * duration)) / RATE
        parts.append((np.sin(2 * np.pi * f * t) + 0.3 * np.sin(6 * np.pi * f * t)) * np.minimum(t / 0.005, 1) * np.exp(-t / (duration / 2.5)))

    buffer = io.BytesIO()

    with wave.open(buffer, 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes((np.concatenate(parts) / 1.3 * VOLUME * 32767).astype(np.int16).tobytes())

    return buffer.getvalue()

def play(kind):
    data = getTone(kind)

    if sys.platform == 'win32':
        import winsound
        threading.Thread(target=winsound.PlaySound, args=(data, winsound.SND_MEMORY), daemon=True).start()
        return True

    player = next((path for path in map(shutil.which, PLAYERS) if path), None)

    if player is None:
        return False

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f'{kind}.wav')

    with open(path, 'wb') as file:
        file.write(data)

    subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
