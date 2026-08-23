from concurrent.futures import ThreadPoolExecutor


def test_create_and_reopen_project(client):
    created = client.post("/api/projects", json={"name": "August rehearsal"})
    assert created.status_code == 201
    project = created.json()

    reopened = client.get(f"/api/projects/{project['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["name"] == "August rehearsal"


def test_blank_project_name_uses_error_contract(client):
    response = client.post("/api/projects", json={"name": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_project_name"


def test_list_projects_returns_persisted_projects(client):
    client.post("/api/projects", json={"name": "First project"})
    client.post("/api/projects", json={"name": "Second project"})

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert {project["name"] for project in response.json()} == {
        "First project",
        "Second project",
    }


def test_unknown_project_uses_error_contract(client):
    response = client.get("/api/projects/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


def test_invalid_project_payload_uses_error_contract(client):
    response = client.post("/api/projects", json={})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["message"] == "Request validation failed"
    assert error["details"]["errors"][0]["loc"] == ["body", "name"]


def test_unknown_route_uses_global_error_contract(client):
    response = client.get("/api/not-a-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Not Found",
            "details": {"status_code": 404},
        }
    }


def test_unsupported_method_uses_global_error_contract(client):
    response = client.put("/api/projects")

    assert response.status_code == 405
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Method Not Allowed",
            "details": {"status_code": 405},
        }
    }


def test_concurrent_project_creates_are_all_persisted(client):
    def create_project(index: int):
        return client.post("/api/projects", json={"name": f"Project {index}"})

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(create_project, range(32)))

    assert [response.status_code for response in responses] == [201] * 32
    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert {project["name"] for project in listed.json()} == {
        f"Project {index}" for index in range(32)
    }
