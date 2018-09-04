import logging
import asyncio
import argparse

import i2plib.sam 
import i2plib.aiosam
import i2plib.utils

BUFFER_SIZE = 65536

async def proxy_data(reader, writer):
    """Proxy data from reader to writer"""
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
    except Exception as e:
        logging.debug('proxy_data_task exception {}'.format(e))
    finally:
        try:
            writer.close()
        except RuntimeError:
            pass
        logging.debug('close connection')

async def client_tunnel(local_address, remote_destination, loop=None, 
                            private_key=None, session_name=None,
                            sam_address=i2plib.sam.DEFAULT_ADDRESS):
    """Add client tunnel to event loop"""
    if not session_name:
        session_name = i2plib.sam.generate_session_id()
        READY = asyncio.Event(loop=loop)
        asyncio.ensure_future(i2plib.aiosam.create_session(session_name,
                    style="STREAM", sam_address=sam_address, loop=loop,
                    session_ready=READY, private_key=private_key), loop=loop)
        await READY.wait()

    async def handle_client(client_reader, client_writer):
        """Handle local client connection"""
        remote_reader, remote_writer = await i2plib.aiosam.stream_connect(
                session_name, remote_destination, sam_address=sam_address,
                loop=loop)
        asyncio.ensure_future(proxy_data(remote_reader, client_writer), 
                              loop=loop)
        asyncio.ensure_future(proxy_data(client_reader, remote_writer),
                              loop=loop)

    asyncio.ensure_future(asyncio.start_server(handle_client, local_address[0], 
                                               local_address[1], loop=loop),
                          loop=loop)

async def server_tunnel(local_address, loop=None, private_key=None, 
                    session_name=None, sam_address=i2plib.sam.DEFAULT_ADDRESS):
    """Add server tunnel to event loop"""
    if not session_name:
        session_name = i2plib.sam.generate_session_id()
        READY = asyncio.Event(loop=loop)
        asyncio.ensure_future(i2plib.aiosam.create_session(session_name,
                    style="STREAM", sam_address=sam_address, loop=loop,
                    session_ready=READY, private_key=private_key), loop=loop)
        await READY.wait()

    async def handle_client(incoming, client_reader, client_writer):
        # data and dest may come in one chunk
        dest, data = incoming.split(b"\n", 1) 
        remote_destination = i2plib.sam.Destination(dest.decode())
        logging.debug("{} client connected: {}.b32.i2p".format(session_name,
            remote_destination.base32))

        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(
                       host=local_address[0], port=local_address[1], loop=loop),
                    timeout=5, loop=loop)
            if data: remote_writer.write(data)
            asyncio.ensure_future(proxy_data(remote_reader, client_writer),
                                  loop=loop)
            asyncio.ensure_future(proxy_data(client_reader, remote_writer),
                                  loop=loop)
        except ConnectionRefusedError:
            client_writer.close()

    async def server_loop():
        while True:
            client_reader, client_writer = await i2plib.aiosam.stream_accept(
                    session_name, sam_address=sam_address, loop=loop)
            incoming = await client_reader.read(BUFFER_SIZE)
            asyncio.ensure_future(handle_client(
                incoming, client_reader, client_writer), loop=loop)

    asyncio.ensure_future(server_loop(), loop=loop)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('type', metavar="TYPE", 
                        help="Tunnel type (server or client)")
    parser.add_argument('address', metavar="ADDRESS", 
                        help="Local address (e.g. 127.0.0.1:8000)")
    parser.add_argument('--debug', '-d', action='store_true',
                        help='Debugging')
    parser.add_argument('--key', '-k', default='', metavar='PRIVATE_KEY',
                        help='Path to private key file')
    parser.add_argument('--destination', '-D', default='', 
                        metavar='DESTINATION', help='Remote destination')
    args = parser.parse_args()

    SAM_ADDRESS = i2plib.utils.get_sam_address()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    loop = asyncio.get_event_loop()
    loop.set_debug(args.debug)

    if args.key:
        private_key = i2plib.sam.PrivateKey(path=args.key)
    else:
        private_key = None

    local_address = i2plib.utils.address_from_string(args.address)

    if args.type == 'client':
        asyncio.ensure_future(client_tunnel(local_address, 
            args.destination, loop=loop, private_key=private_key, 
            sam_address=SAM_ADDRESS), loop=loop)
    elif args.type == 'server':
        asyncio.ensure_future(server_tunnel(local_address, loop=loop,
            private_key=private_key, sam_address=SAM_ADDRESS), loop=loop)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
        loop.close()
