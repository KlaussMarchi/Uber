import os, logging
from Interface.index import Interface
from Utils.variables import DATA_DIR, LOG_PATH
from Worker.index import worker


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(threadName)s] %(levelname)s %(message)s', handlers=[logging.FileHandler(LOG_PATH, encoding='utf-8'), logging.StreamHandler()])
    worker.start()
    app = Interface()
    app.mainloop()
    worker.stop()
