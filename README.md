# imagegen-config

给 Codex 的原版 `imagegen` 加一个本地配置入口：使用自己的 OpenAI Images 兼容 API、key 和模型，不设置环境变量，也不修改原版技能。

**一次安装和配置，以后直接说“生成一张……”即可。** 安装时选择 `--set-default`，将这条偏好写入用户级 `AGENTS.md`。只安装技能时，仍可用 `$imagegen-config` 明确调用。

## 安装并设为默认

需要 Python 3.10+，以及本机已安装的原版 `imagegen` 技能与附带的 `scripts/image_gen.py`。本项目不分发原版技能源码。

```sh
git clone https://github.com/cyx0215/imagegen-config.git
cd imagegen-config
python -m pip install -r requirements.txt
python scripts/install.py --configure --set-default
```

按提示输入 API Base URL、模型名和 key。key 隐藏输入，只写入本地 `config.json`。

**安装后新开一个 Codex 会话**，直接说：

> 生成一张橘猫的图片，写实风格。

> 给这只猫戴一顶小草帽。

默认安装到 `~/.codex/skills/imagegen-config`；如果已有 `CODEX_HOME`，使用该目录。也可以传 `--codex-home PATH` 指定。安装器不创建或修改环境变量。

重复安装会更新程序文件、保留已有 `config.json`，不会重复添加默认路由。重新配置可再次运行 `--configure`，直接按回车保留已有值。

## 为什么需要“设为默认”

Codex 根据技能名称、描述和会话中的指令选择技能。单独放入一个新技能，不会自动覆盖原版 `imagegen` 的选择。

`--set-default` 在全局指令中加入一个带标记的独立区块，声明：普通位图生成、改图优先用 `imagegen-config`，它再复用原版 `imagegen` 的提示词、执行和验图流程。这也记录了用户对配置 API 路径的长期选择，无需每张图片再确认同一路径。

如果存在非空的 `AGENTS.override.md`，安装器会写入该生效文件；否则写入 `AGENTS.md`。其他内容会保留。用户明确指定其他技能/供应商时，以当次请求为准；项目中的其他指令也可能改变选择。这是模型遵循的默认路由偏好，不是一个能强制拦截所有请求的系统开关。已有会话应新开后再验证。

只安装、不设默认：

```sh
python scripts/install.py --configure
```

撤销默认路由，保留技能与 key：

```sh
python scripts/install.py --unset-default
```

## 配置与兼容性

本地配置格式（仓库只提供空 key 的 `config.example.json`）：

```json
{
  "base_url": "https://your-provider.example/v1",
  "api_key": "填写在本地",
  "model": "gpt-image-2"
}
```

- URL 使用供应商实际的 API 前缀，通常包含 `/v1`；不填 `/images/generations` 等完整端点。
- 供应商需要兼容 OpenAI Images 的生成/编辑参数和 `data[].b64_json` 返回格式。只兼容聊天接口、仅返回图片 URL 或要求轮询任务 ID 的服务，不能直接使用。
- 模型名必须通过原版 CLI 的 `gpt-image-*` 校验。供应商自己的同族别名可以配置，但其尺寸、透明背景等能力仍取决于供应商与原版 CLI 的校验。
- 本地曾用供应商别名 `gpt-image-2.6` 验证了单图生成和参考图编辑。这不代表所有供应商或同名模型都兼容。
- key 保存在本地 JSON 文件中，不加密；仓库忽略 `config.json`，安装器也不会复制源目录里的真实配置。
- HTTP 客户端显式使用 config 中的地址和 key；不继承 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、组织/项目或 HTTP 代理设置。需要代理的网络应先适配客户端设置。

## 手动验证与调用

以下示例在安装后的技能目录中执行：

```sh
python scripts/run_imagegen.py --check-config
python scripts/run_imagegen.py generate --prompt "A photorealistic orange tabby cat" --size 1024x1024 --out cat.png --dry-run
python scripts/run_imagegen.py generate --prompt "A photorealistic orange tabby cat" --size 1024x1024 --out cat.png
python scripts/run_imagegen.py edit --image cat.png --prompt "Add a small straw hat; preserve the cat and background" --out cat-hat.png
python scripts/run_imagegen.py generate-batch --input jobs.jsonl --out-dir images
```

`--check-config` 只检查本地字段和原版入口，不访问网络、不显示 key；`--dry-run` 只预览请求。首次真实生成才能验证供应商可用性。

原版技能安装在其他位置时：

```sh
python scripts/run_imagegen.py --imagegen-dir /path/to/original/imagegen generate --prompt "A cat" --out cat.png
```

适配器在独立进程中加载原版脚本，只在内存中绑定客户端工厂、配置检查与默认模型，再调用原版入口。原版文件不写入、不复制。原版内部入口变动可能需要更新适配器；不兼容时会停止。

## 测试

```sh
python -m unittest discover -s tests -p "test_installer.py"
python tests/test_runtime.py
```

安装器测试覆盖重复安装、保留 key、默认路由写入/撤销以及全局 override。运行时测试需要默认位置的原版 `imagegen`，仅访问本机模拟服务，覆盖生成、编辑、异步批量、冲突环境隔离以及原版文件未修改。

原版技能和 Codex 的背景说明：[技能文档](https://learn.chatgpt.com/docs/build-skills)、[AGENTS.md 文档](https://developers.openai.com/codex/guides/agents-md/)。
