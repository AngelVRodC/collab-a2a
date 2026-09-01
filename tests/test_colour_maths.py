"""The colour arithmetic, tested directly.

These functions only run on terminals that cannot redefine colours, which the
rest of the suite never simulates — so a mutation pass found their lines
unprotected: the hue could be rotated by sixty degrees, red and blue swapped,
and white mapped to a grey, with every other test still green.

Tested as arithmetic rather than through curses. What they compute is either
right or wrong on its own, and going through a terminal to find out would only
add a way for the measurement to be the thing that is broken.
"""
from __future__ import annotations

import pytest

from collab.config import rgb_to_256


# --- HSL to RGB --------------------------------------------------------------

def test_black_and_white_land_on_the_ends_of_the_cube():
    """Kills: `min(range(6), …)` → `range(5)`, which loses the top level and
    maps white to a grey."""
    assert rgb_to_256(0, 0, 0) == 16          # the cube's black corner
    assert rgb_to_256(255, 255, 255) == 231   # and its white one


def test_the_channels_are_not_swapped():
    """Kills: red and blue exchanged in the index arithmetic.

    Pure red and pure blue are 36 apart in opposite directions from black;
    swapping them is invisible on a grey and obvious on anything else.
    """
    red = rgb_to_256(255, 0, 0)
    blue = rgb_to_256(0, 0, 255)
    green = rgb_to_256(0, 255, 0)
    assert red == 16 + 36 * 5
    assert green == 16 + 6 * 5
    assert blue == 16 + 5
    assert len({red, green, blue}) == 3


def test_a_colour_maps_to_something_close():
    """The point of the function: not exact, but recognisably the same colour."""
    assert rgb_to_256(0, 204, 204) == 44      # the #008080 this project uses


@pytest.mark.parametrize("rgb", [(0, 0, 0), (255, 255, 255), (128, 128, 128),
                                 (1, 2, 3), (254, 253, 252), (0, 204, 204)])
def test_every_result_is_inside_the_cube(rgb):
    """16-231 is the 6×6×6 cube. Outside it lies the greyscale ramp and the
    sixteen system colours, which the user's terminal theme may have changed to
    anything at all."""
    assert 16 <= rgb_to_256(*rgb) <= 231
