import time

DEFAULT_MODEL = "puter-gpt-4o-mini"

def pick_model(prompt):
    # Returns (model_name, task_type)
    return DEFAULT_MODEL, "general"

def refresh_models():
    return True

def record_perf(model, task, success, latency):
    pass
