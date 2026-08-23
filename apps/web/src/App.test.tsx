import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import App from "./App";

test("renders the Holden Reel welcome copy", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: "Holden Reel" })).toBeInTheDocument();
  expect(
    screen.getByText("Make reels from the comfort of your own holden."),
  ).toBeInTheDocument();
});
