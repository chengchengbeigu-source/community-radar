现象: 改了 GitHub Actions secret，运行时还是报「取 token 失败：返回里没有 access_token」。
根因: secret 的值和本机真实凭据不一致（早期手抄错）。改的时候又卡在加密上。
修法: 用 libsodium 的 `crypto_box_seal` 自己封，别指望 `gh secret set`。

## 为什么 `gh secret set` 用不了

本机装的 gh 是裁剪版，`gh secret set/list` 直接返回 `unsupported`。而
`pip install pynacl` 在 arm64 上**编译失败**（没有预编译 wheel，本地也编不出）。

## 可用路径：ctypes 直接调 libsodium

本机 Termux 自带 libsodium：

```
/data/data/com.dsharnessmobile.shell/files/usr/lib/libsodium.so
```

GitHub secret 的加密算法就是 `crypto_box_seal`（对仓库公钥做匿名 sealed box）：

```python
import ctypes, base64
lib = ctypes.CDLL("/data/data/com.dsharnessmobile.shell/files/usr/lib/libsodium.so")
lib.sodium_init()

def seal(plaintext: bytes, pk_b64: str) -> str:
    pk = ctypes.create_string_buffer(base64.b64decode(pk_b64), 32)
    m  = ctypes.create_string_buffer(plaintext, len(plaintext))
    c  = ctypes.create_string_buffer(len(plaintext) + lib.crypto_box_sealbytes())  # +48
    lib.crypto_box_seal(c, m, ctypes.c_ulonglong(len(plaintext)), pk)
    return base64.b64encode(c.raw).decode()
```

然后 `PUT /repos/{repo}/actions/secrets/{NAME}`，body 为
`{"encrypted_value": seal(...), "key_id": <GET public-key 拿到的 key_id>}`。

## 验证方式

改完 secret 不能只看「写入成功」。要发一个真实推送探针：

```python
from radar.util import qq_send
print(qq_send("测试", "通道验证"))
# 期望输出：qq 已发
```

或者等云端 run 跑完，在日志里 grep `通知结果：` —— 那一行才说明真相。

## 坑

- `qq 取 token 失败：返回里没有 access_token` 这个错误**不一定是凭据错**，
  也可能是 appid/secret/openid 三件套里有一个是错的（三个必须都对）。
- 本机 `_im_qq_creds()` 能取到值，不等于 GitHub Secrets 里的是同一份。
  **两边都要独立验。**
