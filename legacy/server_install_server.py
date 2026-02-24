from claude_code_internal.servers.install_server import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("claude_code_internal.servers.install_server:app", host="0.0.0.0", port=8080, reload=True)

