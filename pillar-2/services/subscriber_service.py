subscribers = []


def subscribe(event_type, callback):
    """
    Register a subscriber for a specific lifecycle event.
    """
    subscribers.append({
        "event_type": event_type,
        "callback": callback
    })


def notify_subscribers(event_type, event_data):
    """
    Notify all subscribers interested in the event.
    """
    notifications = []

    for subscriber in subscribers:
        if subscriber["event_type"] == event_type:
            result = subscriber["callback"](event_data)
            notifications.append(result)

    return notifications


def log_event(event_data):
    """
    Simple subscriber callback for testing.
    """
    return {
        "subscriber": "Lifecycle Logger",
        "event": event_data
    }
