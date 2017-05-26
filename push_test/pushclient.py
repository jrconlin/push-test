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
    """Smoke Test the Autopush push server"""
    def __init__(self, args, loop):
        self.config = args
        self.loop = loop
        self.connection = None
        self.pushEndpoint = None
        self.channelID = None
        self.notifications = []
        self.uaid = None

    def _next_task(self):
        task = self.tasks.pop(0)
        return task[0], task[1]

    async def run(self,
                  server="wss://push.services.mozilla.com"):
        """Connect to a remote server and execute the tasks

        :param server: URL to the Push Server
    
        """
        if not self.connection:
            await self.connect(server)
        self. recv = asyncio.ensure_future(self.receiver())
        cmd, args = self._next_task()
        await getattr(self, cmd)(**args)
        output(status="Done run")

    async def process(self, message):
        """Process an incoming websocket message
        
        :param message: JSON message content
        :return: 
        """
        mtype = "recv_" + message.get('messageType').lower()
        try:
            await getattr(self, mtype)(**message)
        except AttributeError:
            raise PushException("Unknown messageType: {}".format(mtype))

    async def receiver(self):
        """Receiver handler for websocket messages
        
        """
        try:
            while True:
                message = await self.connection.recv()
                await self.process(json.loads(message))
        except websockets.ConnectionClosed:
            pass

    async def send(self, no_recv=False, **msg):
        """Send a message out the websocket connection
        
        :param no_recv: Flag to indicate if response is expected
        :param msg: message content
        :return: 
        """
        output(flow="output", **msg)
        if not self.connection:
            raise PushException("No connection")
        await self.connection.send(json.dumps(msg))
        if no_recv:
            return
        message = await self.connection.recv()
        await self.process(json.loads(message))

    async def connect(self, server=None, **kwargs):
        """Connect to a remote websocket server
        
        :param server: Websocket url
        :param kwargs: ignored
        :return: 
        
        """
        self.connection = await websockets.connect(self.config.server or server)

    async def close(self, **kwargs):
        """Close the websocket connection (if needed)
        
        :param kwargs: ignored
        :return: 
        
        """
        if self.connection and self.connection.state == 1:
            await self.connection.close()
            await self.connection.close_connection()

    async def hello(self, uaid=None, **kwargs):
        """Send a websocket "hello" message
        
        :param uaid: User Agent ID (if reconnecting)
        
        """
        output(status="Sending Hello")
        args = dict(messageType="hello", use_webpush=1, **kwargs)
        if uaid:
            args['uaid'] = uaid
        elif self.uaid:
            args['uaid'] = self.uaid
        await self.send(**args)

    async def ack(self, channelID=None, version=None, **kwargs):
        """Acknowledge a previous mesage
        
        :param channelID: Channel to acknowledge
        :param version: Version string for message to acknowledge
        :param kwargs: Additional optional arguments
        :return: 
        
        """
        last = self.notifications[-1]
        output(status="Sending ACK",
               channelID=channelID or last['channelID'],
               version=version or last['version'])
        await self.send(messageType="ack",
                        channelID=channelID or last['channelID'],
                        version=version or last['version'],
                        no_recv=True)
        cmd, args = self._next_task()
        await getattr(self, cmd)(**args)

    async def register(self, channelID=None, key=None, **kwargs):
        """Register a new ChannelID
        
        :param channelID: UUID for the channel to register
        :param key: applicationServerKey for a restricted access channel
        :param kwargs: additional optional arguments
        :return: 
        
        """
        output(status="Sending new channel registration")
        channelID = channelID or self.channelID or str(uuid.uuid4())
        args = dict(messageType='register',
                    channelID=channelID)
        if key:
            args[key] = key
        args.update(kwargs)
        await self.send(**args)

    async def done(self, **kwargs):
        """Close all connections and mark as done
        
        :param kwargs: ignored
        :return: 
        
        """
        output(status="done")
        await self.close()
        self.recv.cancel()

    async def recv_hello(self, **msg):
        """Process a received "hello"
        
        :param msg: body of response
        :return: 
        
        """
        assert msg['status'] == 200
        try:
            self.uaid = msg['uaid']
            task = self.tasks.pop(0)
            await getattr(self, task[0])(**task[1])
        except KeyError as ex:
            raise PushException from ex

    async def recv_register(self, **msg):
        """Process a received registration message
        
        :param msg: body of response
        :return: 
        
        """
        assert msg['status'] == 200
        try:
            self.pushEndpoint = msg['pushEndpoint']
            self.channelID = msg['channelID']
            output(flow="input",
                   message="register",
                   channelID=self.channelID,
                   pushEndpoint=self.pushEndpoint)
            cmd, args = self._next_task()
            await getattr(self, cmd)(**args)
        except KeyError as ex:
            raise PushException from ex

    async def recv_notification(self, **msg):
        """Process a received notification message
        
        :param msg: body of response
        
        """
        def repad(str):
            return str + '===='[len(msg['data']) % 4:]

        msg['_decoded_data'] = base64.urlsafe_b64decode(
            repad(msg['data'])).decode()
        output(flow="input",
               message="notification",
               **msg)
        self.notifications.append(msg)
        cmd, args = self._next_task()
        await getattr(self, cmd)(**args)

    async def _post(self, session, url, data):
        """Post a message to the endpoint
        
        :param session: async session object
        :param url: pushEndpoint
        :param data: data to send
        :return:
         
        """
        # print ("Fetching {}".format(url))
        with aiohttp.Timeout(10, loop=session.loop):
            return await session.post(url=url, data=data)

    async def _post_session(self, url, headers, data):
        """create a session to send the post message to the endpoint
        
        :param url: pushEndpoint
        :param headers: dictionary of headers
        :param data: body of the content to send
        
        """
        loop = asyncio.get_event_loop()
        async with aiohttp.ClientSession(
                loop=loop,
                headers=headers
        ) as session:
            reply = await self._post(session, url, data)
            body = await reply.text()
            return body

    async def push(self, data=None, headers=None):
        """Push data to the pushEndpoint
        
        :param data: message content
        :param headers: dictionary of headers
        :return: 
        
        """
        if data:
            if not headers:
                headers = {
                    "content-encoding": "aesgcm128",
                    "encryption": "salt=test",
                    "encryption-key": "dh=test",
                }
        result = await self._post_session(self.pushEndpoint, headers, data)
        output(flow="http-out",
               pushEndpoint=self.pushEndpoint,
               headers=headers,
               data=repr(data),
               result=result)
