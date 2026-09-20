/**
 * Undo/redo for the model document.
 *
 * Lifted out of main.js so it can be tested at all. The version that lived
 * there had a guard that made the whole feature unreachable — every edit made
 * through mutate() was dropped and Undo stayed greyed out however much you
 * changed — and nothing caught it, because the logic was tangled with the DOM
 * and the viewer and so had no tests. Anything that decides whether a user
 * can get their work back is worth being able to test on its own.
 *
 * The document is snapshotted whole, as JSON. It is a few kB even for a large
 * study, and copying it entire avoids the class of bug where an undo stack
 * records a change incompletely.
 *
 * View state is deliberately NOT part of a snapshot: undo should take back a
 * change to the model, not move the camera or reopen a panel.
 */

export const UNDO_LIMIT = 60;

export class History {
  /**
   * @param {() => string} read   current document, serialised
   * @param {(s: string) => void} write   restore a serialised document
   */
  constructor(read, write) {
    this.read = read;
    this.write = write;
    this.undoStack = [];
    this.redoStack = [];
    // The state as of the END of the last committed edit.
    //
    // Undo pushes THIS rather than a snapshot taken when the edit begins,
    // because a dozen call sites change the model and then call mutate(() =>
    // {}) purely to save and re-render. Snapshotting on entry captured those
    // edits after they had already happened, so undoing them was a no-op —
    // which is what deleting a bolt and pressing undo used to demonstrate.
    // Taking it at the end of the previous edit is correct for both shapes
    // and does not depend on every future call site knowing which it is.
    this.baseline = null;
  }

  get canUndo() { return this.undoStack.length > 0; }
  get canRedo() { return this.redoStack.length > 0; }

  /** Start tracking from the document as it stands, discarding any history.
   *  A different project must not be undoable into the previous one. */
  reset() {
    this.undoStack.length = 0;
    this.redoStack.length = 0;
    this.baseline = this.read();
  }

  /**
   * Record the pre-edit state. Called BEFORE the change, so `read()` still
   * returns the baseline — which is exactly why this must not test them
   * against each other. `baseline` is the state to go back to; push it.
   */
  push() {
    if (this.baseline === null) return;
    this.undoStack.push(this.baseline);
    if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
    this.redoStack.length = 0;        // a new edit forks the future
  }

  /** Mark the end of an edit: this is where the next undo will return to. */
  commit() { this.baseline = this.read(); }

  /**
   * For edits that did not go through push()/commit() — typing, which does
   * not re-render the panel under the caret and so commits on blur. Records
   * one step per field rather than one per keystroke, and only if something
   * actually changed.
   */
  commitIfChanged() {
    if (this.baseline === null) return false;
    if (this.read() === this.baseline) return false;
    this.undoStack.push(this.baseline);
    if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
    this.redoStack.length = 0;
    this.commit();
    return true;
  }

  undo() { return this._step(this.undoStack, this.redoStack); }
  redo() { return this._step(this.redoStack, this.undoStack); }

  _step(from, to) {
    if (!from.length) return false;
    to.push(this.read());
    this.write(from.pop());
    this.commit();
    return true;
  }
}
