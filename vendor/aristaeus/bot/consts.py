import numpy as np

# cannon rush tools
BLOCKING = "blocking"
FINAL_PLACEMENT = "final_placement"
LOCATION = "location"
POINTS = "points"
SCORE = "score"
TYPE_ID = "type_id"
WEIGHT = "weight"

"""CannonCalculations"""
A, B, C, D, E, F, G, H, I, J, K, L = [2**i for i in range(12)]
Z = 2**12

"""
Set up so that the middle will result in values >= 4096
and the convolution will look like it's using
    [[A, B, C, D],
     [E, Z, Z, F],
     [G, Z, Z, H],
     [I, J, K, L]]
when compared to the map.
"""

DESIRABILITY_KERNEL = np.array([[D, F, H, L], [C, Z, Z, K], [B, Z, Z, J], [A, E, G, I]])

ALL_LETTERS = A + B + C + D + E + F + G + H + I + J + K + L
TOP = A + B + C + D
LEFT = A + E + G + I
BOTTOM = I + J + K + L
RIGHT = D + F + H + L
ALL_CORNERS = A + D + I + L

INVALID_BLOCK = {
    # nothing
    0,
    # everything
    ALL_LETTERS,
    # single values
    A,
    B,
    C,
    D,
    E,
    F,
    G,
    H,
    I,
    J,
    K,
    L,
    # adjacent pairs
    A + B,
    B + C,
    C + D,
    A + E,
    E + G,
    G + I,
    I + J,
    J + K,
    K + L,
    L + H,
    H + F,
    F + D,
    # adjacent triple
    A + B + C,
    B + C + D,
    A + E + G,
    E + G + I,
    I + J + K,
    J + K + L,
    L + H + F,
    H + F + D,
    # all one side
    A + B + C + D,
    A + E + G + I,
    I + J + K + L,
    L + H + F + D,
    # two diagonals
    A + D,
    A + I,
    A + L,
    D + I,
    D + L,
    I + L,
    # already blocked corner (length 1)
    A + B + E,
    A + B + C + E,
    A + B + C + D + E,
    A + E + G + B,
    A + E + G + I + B,
    G + I + J,
    E + G + I + J,
    A + E + G + I + J,
    I + J + K + G,
    I + J + K + L + G,
    K + L + H,
    J + K + L + H,
    I + J + K + L + H,
    L + H + F + K,
    L + H + F + D + K,
    F + D + C,
    H + F + D + C,
    L + H + F + D + C,
    B + C + D + F,
    A + B + C + D + F,
    # already blocked corner (length 2)
    A + B + C + E + G,
    A + B + C + E + G + I,
    E + G + I + J + K,
    A + E + G + I + J + K,
    J + K + L + H + F,
    J + K + L + H + F + D,
    H + F + D + C + B,
    L + H + F + D + C + B,
    # already blocked corner (length 3)
    A + B + C + D + E + G + I,
    A + E + G + I + J + K + L,
    I + J + K + L + H + F + D,
    L + H + F + D + C + B + A,
    # full side + 2 that were somehow missing
    TOP + E + G,
    TOP + F + H,
    BOTTOM + G + E,
    BOTTOM + H + F,
    # pocket
    E + A + B + C + D + F,
    G + E + A + B + C + D + F,
    E + A + B + C + D + F + H,
    G + E + A + B + C + D + F + H,
    I + G + E + A + B + C + D + F,
    I + G + E + A + B + C + D + F + H,
    L + H + F + D + C + B + A + E,
    L + H + F + D + C + B + A + E + G,
    I + G + E + A + B + C + D + F + H + L,
    # pocket
    B + A + E + G + I + J,
    B + A + E + G + I + J + K,
    C + B + A + E + G + I + J,
    C + B + A + E + G + I + J + K,
    D + C + B + A + E + G + I + J,
    D + C + B + A + E + G + I + J + K,
    B + A + E + G + I + J + K + L,
    C + B + A + E + G + I + J + K + L,
    D + C + B + A + E + G + I + J + K + L,
    # pocket
    G + I + J + K + L + H,
    E + G + I + J + K + L + H,
    G + I + J + K + L + H + F,
    E + G + I + J + K + L + H + F,
    A + E + G + I + J + K + L + H,
    A + E + G + I + J + K + L + H + F,
    G + I + J + K + L + H + F + D,
    A + E + G + I + J + K + L + H + F,
    A + E + G + I + J + K + L + H + F + D,
    # pocket
    K + L + H + F + D + C,
    J + K + L + H + F + D + C,
    K + L + H + F + D + C + B,
    J + K + L + H + F + D + C + B,
    I + J + K + L + H + F + D + C,
    I + J + K + L + H + F + D + C + B,
    K + L + H + F + D + C + B + A,
    I + J + K + L + H + F + D + C + B,
    I + J + K + L + H + F + D + C + B + A,
    # side + diagonal pocket
    B + E + G + J,
    B + A + E + G + J,
    B + E + G + I + J,
    B + E + G,
    E + G + J,
    # side + diagonal pocket
    G + J + K + H,
    G + I + J + K + H,
    G + J + K + L + H,
    G + J + K,
    J + K + H,
    # side + diagonal pocket
    K + H + F + C,
    K + L + H + F + C,
    K + H + F + D + C,
    K + H + F,
    H + F + D,
    # side + diagonal pocket
    F + C + B + E,
    F + D + C + B + E,
    F + C + B + A + E,
    F + C + B,
    B + C + E,
    # everything except one
    ALL_LETTERS - A,
    ALL_LETTERS - B,
    ALL_LETTERS - C,
    ALL_LETTERS - D,
    ALL_LETTERS - E,
    ALL_LETTERS - F,
    ALL_LETTERS - G,
    ALL_LETTERS - H,
    ALL_LETTERS - I,
    ALL_LETTERS - J,
    ALL_LETTERS - K,
    # only missing two corners
    ALL_LETTERS - A - D,
    ALL_LETTERS - A - L,
    ALL_LETTERS - A - I,
    ALL_LETTERS - D - L,
    ALL_LETTERS - D - I,
    ALL_LETTERS - I - L,
    # only missing three corners
    ALL_LETTERS - ALL_CORNERS + D,
    ALL_LETTERS - ALL_CORNERS + I,
    ALL_LETTERS - ALL_CORNERS + A,
    ALL_LETTERS - ALL_CORNERS + L,
    # only missing all corners
    ALL_LETTERS - ALL_CORNERS,
}
