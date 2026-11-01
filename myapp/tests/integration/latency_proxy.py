"""MySQL 前面的 TCP proxy：計算網路往返次數（RT），也能加延遲、斷線、讓連線卡住（ENG-01、ENG-02、ENG-21）。

改寫自 docs/rebuild/frontend-rebuild/prototypes/eng/tools/delayproxy.py。

- RT 的算法：伺服器回過話之後（或連線剛開始），用戶端又開始送資料，就算一次往返。
  測試只斷言 RT，不斷言時間，所以預設不加延遲。
- `cmds` 依 MySQL 封包的指令位元組分類（QUERY、PING…）。走 TLS 時看不到內容，只有關掉 TLS 才準。
- `kill()`：切斷所有連線，之後的連線被拒絕（像資料庫重開機）。
- `stall()`：連線還在，但資料不再轉送；新的連線接受之後也不回應（像 Wi-Fi 斷掉、封包被丟掉）。
"""
import queue
import socket
import threading
import time

import pytest

COMMANDS = {0x01: "QUIT", 0x03: "QUERY", 0x0E: "PING", 0x16: "PREPARE", 0x17: "EXECUTE"}


class LatencyProxy:
    def __init__(self, upstream_host, upstream_port, one_way_delay=0.0):
        self.upstream = (upstream_host, upstream_port)
        self.delay = one_way_delay
        self._lock = threading.Lock()
        self._flowing = threading.Event()  # 清掉＝stall：資料停在 proxy 裡
        self._flowing.set()
        self._closed = False
        self._sockets = []
        self.reset()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._accept, name="latency-proxy", daemon=True).start()

    def reset(self):
        with self._lock:
            self.rt = self.bytes_c2s = self.bytes_s2c = self.connections = 0
            self.cmds = {}

    def snapshot(self):
        """{"rt", "c2s", "s2c", "conns", "cmds"}：上次 reset() 之後的累計。"""
        with self._lock:
            return dict(rt=self.rt, c2s=self.bytes_c2s, s2c=self.bytes_s2c, conns=self.connections,
                        cmds=dict(self.cmds))

    def stall(self):
        self._flowing.clear()

    def kill(self):
        """切斷所有連線，並且不再接受新的連線（port 會拒絕連線）。"""
        self.close()

    def close(self):
        self._closed = True
        self._flowing.set()  # 放開停在 stall 的轉送執行緒，讓它們結束
        with self._lock:
            sockets, self._sockets = [self._listener, *self._sockets], []
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _track(self, sock):
        with self._lock:
            if self._closed:
                sock.close()
                return False
            self._sockets.append(sock)
            return True

    def _accept(self):
        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            if not self._track(client):
                return
            if not self._flowing.is_set():
                continue  # stall 中：接受連線但永遠不回應，用戶端只能等到逾時
            try:
                server = socket.create_connection(self.upstream, timeout=5)
            except OSError:
                client.close()
                continue
            server.settimeout(None)
            if not self._track(server):
                return
            for sock in (client, server):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            state = {"last": None, "first_s2c": True}
            with self._lock:
                self.connections += 1
            for src, dst, direction in ((client, server, "c2s"), (server, client, "s2c")):
                chunks = queue.Queue()
                threading.Thread(target=self._pump, args=(src, chunks, direction, state), daemon=True).start()
                threading.Thread(target=self._send, args=(dst, chunks), daemon=True).start()

    def _pump(self, src, chunks, direction, state):
        while True:
            try:
                data = src.recv(65536)
            except OSError:
                data = b""
            now = time.perf_counter()
            if not data:
                chunks.put((now, None))
                return
            extra = 0.0
            with self._lock:
                if direction == "c2s":
                    if state["last"] != "c2s":
                        self.rt += 1
                        # MySQL 封包：3 bytes 長度、1 byte 序號；序號 0 的第一個 byte 是指令
                        if len(data) >= 5:
                            name = COMMANDS.get(data[4], "other") if data[3] == 0 else "handshake"
                            self.cmds[name] = self.cmds.get(name, 0) + 1
                    self.bytes_c2s += len(data)
                else:
                    self.bytes_s2c += len(data)
                    if state["first_s2c"]:
                        extra = 2 * self.delay  # 補上 TCP 握手的那一個 RTT（本機連線時沒有付）
                        state["first_s2c"] = False
                state["last"] = direction
            chunks.put((now + self.delay + extra, data))

    def _send(self, dst, chunks):
        while True:
            due, data = chunks.get()
            self._flowing.wait()
            wait = due - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            if self._closed:
                return
            try:
                if data is None:
                    dst.shutdown(socket.SHUT_WR)
                    return
                dst.sendall(data)
            except OSError:
                return


@pytest.fixture
def db_proxy(db, mysql_server, monkeypatch):
    """讓 app 的 DB_HOST／DB_PORT 改走 proxy；其他 DB_* 由 db fixture 設好。回傳 proxy。"""
    proxy = LatencyProxy(mysql_server["host"], mysql_server["port"])
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", str(proxy.port))
    yield proxy
    proxy.close()
