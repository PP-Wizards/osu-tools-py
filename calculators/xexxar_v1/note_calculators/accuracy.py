import math

def dt_to_d(dt):
    dt = min(dt, 1000)
    return math.sin(math.pi * dt / 2000)

def calculate_accuracy_difficulty(object: dict, index, metadata: dict, objects: list):
    d = 0

    ## essentially, objects are harder to acc the slower they are.
    if object['type'] == 'circle':
        d = dt_to_d(object['dt']) / metadata['300_window']
    else:
        d = dt_to_d(object['dt']) / metadata['50_window']

    # Need to add complexity bonus for trick rhythms

    return d
