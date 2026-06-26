from pydantic import BaseModel


class CreatePlanRequest(BaseModel):
    user_id: str                 # every request is scoped to a user
    user_input: str
    use_cache: bool = True


class NodeFailedRequest(BaseModel):
    user_id: str
    node_id: str                 # the broken node id (from the plan's node_path)
