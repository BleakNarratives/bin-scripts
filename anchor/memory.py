# In-memory history buffer
_history = []

def get_recent(n=6):
    return _history[-n:]

def push(role, text):
    _history.append({"role": role, "content": text})
