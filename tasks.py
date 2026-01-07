from invoke import task


@task
def deploy(c):
    """
    Build the Docker image and push it to the registry.
    """
    registry = "192.168.0.191:5000"
    image_name = "trader"
    tag = "latest"
    full_image_name = f"{registry}/{image_name}:{tag}"

    print(f"Building {full_image_name}...")
    c.run(f"docker build -t {full_image_name} .")

    print(f"Pushing {full_image_name}...")
    c.run(f"docker push {full_image_name}")
