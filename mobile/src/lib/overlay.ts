/**
 * Full-screen layers (the camera) that Android's back button closes first,
 * before it closes the drawer, changes tab or minimises the app.
 */
const stack: (() => void)[] = [];

/** Register a layer's close function; returns the function that unregisters it. */
export function pushOverlay(close: () => void): () => void {
  stack.push(close);
  return () => {
    const index = stack.lastIndexOf(close);
    if (index >= 0) stack.splice(index, 1);
  };
}

/** Close the top layer. False when there is none. */
export function closeTopOverlay(): boolean {
  const close = stack.pop();
  if (!close) return false;
  close();
  return true;
}
