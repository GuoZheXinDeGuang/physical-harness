---
name: place_on
description: Place one held object stably on top of a named support object.
---

# Place On

Use this skill only after the robot is holding `object`. The benchmark backend
owns the motion and contact controller; the skill succeeds only when `object`
is released and stably supported by `target`.
