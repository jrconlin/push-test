import asyncio
import base64
import json
import uuid

import aiohttp
import websockets

class PushException(Exception):
    pass


def output(**msg):
    print(json.dumps(msg))

class PushClient(object):
    def __init__(self, args, loop):
        self.config = args
        self.loop = loop
        self.connection = None
        self.pushEndpoint = None
        self.channelID = None
        self.notifications = []

    async def run(self,
                  server="wss://push.services.mozilla.com"):
        if not self.connection:
            self.connection = await websockets.connect(
                server or self.args.server)
        self.recv = asyncio.ensure_future(self.receiver())
        task = self.tasks.pop(0)
        await getattr(self, task[0])(**task[1])
        output(status="Done run")

    async def process(self, message):
        mtype = "recv_" + message.get('messageType').lower()
        try:
            await getattr(self, mtype)(**message)
        except AttributeError:
            raise PushException("Unknown messageType: {}".format(mtype))

    async def receiver(self):
        try:
            while True:
                message = await self.connection.recv()
                await self.process(json.loads(message))
        except websockets.ConnectionClosed:
            pass

    async def send(self, no_recv=False, **kwargs):
        output(flow="output", **kwargs)
        msg = kwargs
        if not self.connection:
            raise PushException("No connection")
        await self.connection.send(json.dumps(msg))
        if no_recv:
            return
        message = await self.connection.recv()
        await self.process(json.loads(message))

    async def hello(self, uaid=None, **kwargs):
        output(status="Sending Hello")
        await self.send(messageType="hello", use_webpush=1, **kwargs)

    async def ack(self, channelID=None, version=None, **kwargs):
        last = self.notifications[-1]
        output(status="Sending ACK",
               channelID=channelID or last['channelID'],
               version=version or last['version'])
        await self.send(messageType="ack",
                        channelID=channelID or last['channelID'],
                        version=version or last['version'],
                        no_recv=True)
        task = self.tasks.pop(0)
        await getattr(self, task[0])(**task[1])

    async def register(self, channelID=None, key=None, **kwargs):
        output(status="Sending new channel registration")
        channelID = channelID or self.channelID or str(uuid.uuid4())
        args = dict(messageType='register',
                    channelID=channelID)
        if key:
            args[key] = key
        args.update(kwargs)
        await self.send(**args)

    async def done(self, **kwargs):
        output(status="done")
        await self.connection.close()
        self.recv.cancel()

    async def recv_hello(self, **msg):
        assert msg['status'] == 200
        try:
            self.uaid = msg['uaid']
            task = self.tasks.pop(0)
            await getattr(self, task[0])(**task[1])
        except KeyError as ex:
            raise PushException from ex

    async def recv_register(self, **msg):
        assert msg['status'] == 200
        try:
            self.pushEndpoint = msg['pushEndpoint']
            self.channelID = msg['channelID']
            output(flow="input",
                   message="register",
                   channelID=self.channelID,
                   pushEndpoint=self.pushEndpoint)
            task = self.tasks.pop(0)
            await getattr(self, task[0])(**task[1])
        except KeyError as ex:
            raise PushException from ex

    async def recv_notification(self, **msg):
        def repad(str):
            return str + '===='[len(msg['data']) % 4:]

        msg['_decoded_data'] = base64.urlsafe_b64decode(repad(msg['data']))
        output(flow="input",
               message="notification",
               data=(msg["_decoded_data"].decode()))
        self.notifications.append(msg)
        task = self.tasks.pop(0)
        await getattr(self, task[0])(**task[1])

    async def _fetch(self, session, url, data):
        # print ("Fetching {}".format(url))
        with aiohttp.Timeout(10, loop=session.loop):
            return await session.post(url=url, data=data)

    async def post(self, url, headers, data):
        loop = asyncio.get_event_loop()
        async with aiohttp.ClientSession(
                loop=loop,
                headers=headers
        ) as session:
            reply = await self._fetch(session, url, data)
            body = await reply.text()
            return body

    async def push(self, data=None, headers=None):
        if data:
            if not headers:
                headers = {
                    "content-encoding": "aesgcm128",
                    "encryption": "salt=test",
                    "encryption-key": "dh=test",
                }
        result = await self.post(self.pushEndpoint, headers, data)
        output(flow="http-out",
               pushEndpoint=self.pushEndpoint,
               headers=headers,
               data=repr(data),
               result=result)
