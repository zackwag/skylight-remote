"""GATT proxy client: connects to a provisioned mesh node and
sends/receives network PDUs (proxy PDU type 0x00)."""

import asyncio

from bleak import BleakClient

from . import network
from .provisioner import SarReassembler

PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"


class MeshProxy:
    """Connection + access messaging to exactly one node (the lamp)."""

    def __init__(self, address: str, ctx: network.NetContext, src: int,
                 log=print):
        self.address = address
        self.ctx = ctx
        self.src = src
        self.log = log
        self.client: BleakClient | None = None
        self._sar = SarReassembler()
        self._rx: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self):
        last_err = None
        for attempt in range(4):
            if attempt:
                self.log(f"Connection failed ({last_err}), "
                         f"attempt {attempt + 1}/4 ...")
                await asyncio.sleep(2 * attempt)
            self.client = BleakClient(self.address, timeout=30)
            try:
                await self.client.connect()
                break
            except Exception as e:
                last_err = e
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
        else:
            raise last_err

        def on_notify(_, data: bytearray):
            frame = self._sar.feed(bytes(data))
            if frame is None or (data[0] & 0x3F) != 0x00:
                return
            decoded = network.decode_network_pdu(self.ctx, frame)
            if decoded:
                self._rx.put_nowait(decoded)

        await self.client.start_notify(PROXY_DATA_OUT, on_notify)
        return self

    async def __aexit__(self, *exc):
        try:
            await self.client.disconnect()
        except Exception:
            pass

    async def send_network_pdu(self, pdu: bytes):
        await self.client.write_gatt_char(
            PROXY_DATA_IN, bytes([0x00]) + pdu, response=False)

    async def send_access(self, seq_state, key: bytes, is_app_key: bool,
                          dst: int, opcode: int, params: bytes, ttl: int = 5):
        """Sends an access message; seq_state is a dict with "seq"
        that must be persisted."""
        access = network.encode_access(opcode, params)
        seq = seq_state["seq"]
        pdus = network.build_transport_pdus(
            self.ctx, key, is_app_key, seq, self.src, dst, access)
        for i, transport in enumerate(pdus):
            net = network.encode_network_pdu(
                self.ctx, 0, ttl, seq + i, self.src, dst, transport)
            await self.send_network_pdu(net)
            await asyncio.sleep(0.05)
        seq_state["seq"] = seq + len(pdus)

    async def wait_status(self, key: bytes, is_app_key: bool,
                          expect_opcode: int, timeout: float = 10):
        """Waits for a status message with the expected opcode."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"No response 0x{expect_opcode:04x} after {timeout}s")
            ctl, ttl, seq, src, dst, transport = await asyncio.wait_for(
                self._rx.get(), timeout=remaining)
            if ctl:
                continue                   # segment acks etc.
            access = network.decrypt_access(
                self.ctx, key, is_app_key, seq, src, dst, transport)
            if access is None:
                continue
            opcode, params = network.parse_access(access)
            if opcode == expect_opcode:
                return params
            self.log(f"  (response 0x{opcode:04x} ignored)")
