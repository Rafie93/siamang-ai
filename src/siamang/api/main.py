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
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, workers=1, access_log=False)


if __name__ == '__main__':
    main()
