def upload_file_to_mega(file_path, folder_name):
    filename = os.path.basename(file_path)

    # Удаляем файл, если уже существует
    try:
        run_megatools_command([
            "megarm",
            f"/Root/{folder_name}/{filename}"
        ])
        logger.info("MEGA: old file removed -> %s", filename)
    except Exception:
        logger.info("MEGA: file not exists, skip remove -> %s", filename)

    # Загружаем файл
    run_megatools_command([
        "megaput",
        "--path", f"/Root/{folder_name}/",
        file_path
    ])
