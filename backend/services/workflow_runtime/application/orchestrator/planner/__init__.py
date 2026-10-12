__all__ = ["create_plan", "report_node_failure", "restore_plan"]


def __getattr__(name: str):
    if name in __all__:
        from . import service

        return getattr(service, name)
    raise AttributeError(name)
