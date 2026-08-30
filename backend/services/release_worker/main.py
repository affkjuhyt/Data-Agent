from services.release_worker.app import create_app


app = create_app()


if __name__ == "__main__":
    from services.release_worker.release_core import run_server

    run_server(app)
