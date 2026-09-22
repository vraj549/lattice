# Viewport navigation

Lattice's viewport is mapped to **Siemens NX**. If you drive NX, the mouse
already works; nothing here should need reading.

## Mouse

| Input | Action |
|---|---|
| MB2 drag | Rotate |
| Shift + MB2 | Pan |
| MB2 + MB3 | Pan |
| Ctrl + MB2 | Zoom |
| MB1 + MB2 | Zoom |
| Wheel | Zoom, toward the cursor |
| MB1 | Select — never navigation |

Chords are read live, so pressing MB3 while MB2 is already down turns a rotate
into a pan without letting go.

Rolling the wheel forward zooms in, and dragging up with Ctrl+MB2 zooms in.
Two zoom gestures disagreeing inside one viewport is worse than either
direction on its own.

Zoom is toward the cursor rather than the screen centre. NX offers this as a
preference; it is the default here because zooming to the centre means
re-panning constantly to inspect a corner of a model.

## Where the drag starts

A constrained rotation is decided from where the cursor was when the drag
**started**, not from where it wanders to:

| Start the drag | Rotation |
|---|---|
| Near the left or right edge | About the screen's horizontal axis only |
| Near the bottom edge | About the screen's vertical axis only |
| Near the top edge | Spin about the axis normal to the screen |

The band is 8 % of the window, clamped to 24–80 px, and is suppressed
entirely on a viewport too small to have a meaningful middle. A corner belongs
to the top/bottom band: the test is ordered so the result is predictable from
the cursor rather than incidental.

## Rotation is a free trackball

The drag axes are the screen's own, taken from the camera's current
orientation. There is no world "up" the camera is hung from, so there is no
orientation in which the controls degrade, and the model can be tumbled
without limit. Use a standard view (keys `1`–`6`, or `0` for isometric) to get
back to a level horizon.

This matters because the previous controller was a **turntable** — an azimuth
about world Z and a polar angle down from it — and a turntable has poles. The
Top view sat exactly on one. Measured on the old controller, for a half-radian
horizontal drag:

| View | Camera travel, as a fraction of the view distance |
|---|---|
| Isometric | 43 % |
| Front | 49 % |
| 0.2 rad off vertical | 10 % |
| **Top** | **0.05 %** |

At the Top view a horizontal drag spun the model in place instead of orbiting
it. The epsilon the old Top and Bottom views were nudged by — a thousandth of
a radian off the pole — existed to paper over the same defect, because looking
straight down the up-vector leaves the camera's roll undefined. Orientation is
a quaternion now, for the same reason: Euler angles would put the gimbal back.

The equivalent measurement on the current controller is 82 % from every
standard view, Top included, and it is asserted in `tests/test_nav.mjs`.

## Trackpads

NX gives MB2 every navigation verb, and a trackpad has no middle button. The
shortcuts panel (`?`) has a **left button also rotates** option for that. It is
off by default and it is a deviation from NX: with it on, MB1 can no longer be
a plain selection drag.

## SpaceMouse

A 3Dconnexion SpaceMouse connects over **WebHID**, through the ⊕ button in the
toolbar. Lattice talks to the device directly rather than through 3DxWare,
because a self-hosted page cannot see a vendor driver's events.

- **Chromium only** — Chrome, Edge, Brave, Opera. Firefox and Safari have not
  shipped WebHID, and the button says so rather than failing silently.
- A secure context is required. `localhost` counts, so a normal Lattice
  session qualifies.
- The browser asks which device to grant, and that prompt needs a click — which
  is why connecting is a button and not something that happens on load. Once
  granted, Lattice reopens the device by itself on the next visit.

Axis mapping, in the 3Dconnexion convention (+X right, +Y away from you,
+Z up):

| Cap | Viewport |
|---|---|
| Slide left/right | Pan horizontally |
| Slide up/down | Pan vertically |
| Push/pull | Zoom |
| Tilt forward/back | Rotate about the screen's horizontal axis |
| Tilt left/right | Roll about the screen normal |
| Twist | Rotate about the screen's vertical axis |

The device is a velocity controller: it reports how far the cap is pushed, not
how far anything moved, and keeps reporting while it is held. Lattice
integrates that against real frame time, so the speed does not depend on the
frame rate. There is a 6 % deadzone, subtracted rather than clipped — a
SpaceMouse does not return to exactly zero at rest, and clipping would put a
step at the edge of the deadzone so the model jumped as it started moving.

## Sources

The NX behaviour above is from Siemens' own documentation and NX community
threads, not from memory:

- Siemens NX help, *Rotate about a center of rotation* — the edge zones and
  which axis each one locks.
- [Siemens community: NX zoom, pan, rotate](https://community.sw.siemens.com/s/question/0D54O000061xMGxSAM/nx-zoom-pan-rotate-speed)
- [Understanding the functions of the mouse buttons (UG/NX)](http://ugs-tutor.blogspot.com/2008/06/understanding-functions-of-mouse.html)
- [Siemens community: mouse wheel zoom direction](https://community.sw.siemens.com/s/question/0D54O000061xHB2SAM/mouse-wheel-zoom-in-zoom-out)
  — the wheel direction default and that Ctrl+MB2 follows the same preference.
