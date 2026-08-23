import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { ApiClient, Project } from "../../types";
import { ProjectStart } from "./ProjectStart";

const rehearsal: Project = {
  id: "p1",
  name: "Rehearsal",
  created_at: "2026-08-23T12:00:00+00:00",
  updated_at: "2026-08-23T12:00:00+00:00",
};

function fakeApi(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    createProject: vi.fn().mockResolvedValue(rehearsal),
    listProjects: vi.fn().mockResolvedValue([]),
    getProject: vi.fn().mockResolvedValue(rehearsal),
    importMedia: vi.fn().mockResolvedValue({ assets: [] }),
    listMedia: vi.fn().mockResolvedValue({ assets: [] }),
    composePlan: vi.fn(),
    startRender: vi.fn(),
    getJob: vi.fn(),
    cancelJob: vi.fn(),
    ...overrides,
  };
}

it("creates a named project and advances", async () => {
  // Break caught: submitting the form no longer creates the entered project or opens it.
  const api = fakeApi();
  const onOpen = vi.fn();
  const user = userEvent.setup();
  render(<ProjectStart api={api} onOpen={onOpen} />);

  await user.type(screen.getByLabelText(/project name/i), "Rehearsal");
  await user.click(screen.getByRole("button", { name: /create project/i }));

  expect(api.createProject).toHaveBeenCalledWith("Rehearsal");
  expect(onOpen).toHaveBeenCalledWith(rehearsal);
});

it("shows recent projects as buttons that open a project", async () => {
  // Break caught: a recent project is displayed but cannot be reopened.
  const onOpen = vi.fn();
  const user = userEvent.setup();
  render(<ProjectStart api={fakeApi({ listProjects: vi.fn().mockResolvedValue([rehearsal]) })} onOpen={onOpen} />);

  await user.click(await screen.findByRole("button", { name: /open rehearsal/i }));

  expect(onOpen).toHaveBeenCalledWith(rehearsal);
});
