"""Local API entry point; one process owns the proposal worker."""
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog='siamang-api')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    import uvicorn
    from .app import create_app
    app = create_app()

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # uvicorn waits for open connections before it shuts the app down, and an event stream never finishes alone.
            if app.state.runtime.hub:
                app.state.runtime.hub.end_streams()
            super().handle_exit(sig, frame)

    Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, workers=1, access_log=False,
                          timeout_graceful_shutdown=5)).run()


if __name__ == '__main__':
    main()
