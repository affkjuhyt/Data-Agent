from services.backend import create_app


app = create_app()


if __name__ == "__main__":
    from release_core import run_server

    run_server(app)
