# Vietnamese input and control focus

## Behaviour

Text fields and text areas use neutral borders on mouse focus. Keyboard focus
remains visible with a contrasting neutral outline. This does not change the
copper selection/resize markers for content on the preview or timeline.

The shared text controls request `ImhNoPredictiveText`, not a Latin-only input
mask. Vietnamese Unicode and IME composition remain supported. Text areas use
plain text, so pasted subtitle/caption text is never interpreted as HTML.

The application input filter finishes **active** composition before cursor
navigation, changing focus, closing a window, or handling a pointer press. It
does not commit ordinary edits/backspaces. Pointer commits happen at the
QQuickWindow boundary, not again for each QML recipient of the same event.
The recursion guard remains in place before inspecting nested native events.

Auto subtitle saves wait until composition finishes. Echoing the saved text
does not reinstall an unchanged editor value or reset the cursor/undo stack.
Manual subtitle model refreshes do not overwrite a live composition. Watermark
fields keep a local edit instead of binding the editable text directly to a
model that can refresh in the background.

This follows Qt's [composition guidance](https://doc.qt.io/qt-6/qml-qtquick-textedit.html#inputMethodComposing-prop)
and [commit API](https://doc.qt.io/qt-6/qinputmethod.html#commit).

## Verification

`tests/test_input_method_commit_filter.py` covers the event policy and recursive
events. `tests/test_vietnamese_input_ui.py` uses the production QML and real
`QInputMethodEvent` messages for composition, replacement ranges, arrow/mouse
cursor movement, model echoes, delayed saves, watermark saves and focus visuals.
It preserves both composed and decomposed Unicode, rather than normalizing text
while the user is editing.

These tests can run with the native Windows Qt platform as well as offscreen.
They simulate the IME protocol; they do **not** operate the Windows Vietnamese
Telex text service itself. Check that service manually after restarting the
source app:

1. Select Vietnamese Telex with Win+Space. Enter a sentence in a subtitle field,
   including `Đường phố`, `tiếng Việt`, `người`, and `quyền`.
2. While the last word is still being composed, use Left/Right, Home/End and
   click near the beginning/middle/end; continue typing and deleting characters.
3. Repeat in Manual/Auto subtitles, watermark, project rename, search and social
   captions. In Auto, pause more than 300 ms mid-word before finishing it.
4. Click Save or outside the field, switch subtitle, close/reopen the dialog and
   reopen the project. Confirm the final word is saved once and no text is lost.
5. Check that Tab/Shift+Tab retains a visible neutral focus indicator and that
   mouse clicks/open dropdowns do not show the old gold border.
