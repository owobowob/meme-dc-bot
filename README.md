# Meme AI · Discord 梗圖推薦 Bot

用一句話或頻道近期對話找梗圖。Bot 優先搜尋 SQLite 圖庫，並可使用 MemeStack 或 Wikimedia Commons 補足結果。

## 檢索流程

1. **受控標籤初篩（Recall）**。Gemini 或 OpenAI 將聊天語境映射到 `data/known_tags.json` 中的標籤。例如「還能重歸於好嗎」可依語氣對應 `pleading`、`relationship_drama` 或 `regret`。若模型把已知標籤誤放到外部搜尋欄位，程式會先撈回這些標籤查 SQLite。日常對話優先使用情境與情緒標籤，作品標籤只在點名作品或有可辨識的台詞、角色等線索時使用。
2. **描述重排（Precision）**。SQLite 依標籤交集取回本伺服器及全域候選。最高同分候選以 `description` 與對話的詞語重疊度重排，並檢查排序後前五張候選圖片的可用性。重排在本地執行。
3. **外部搜尋**。語意上找不到接近的已知標籤時，模型會產生一到三個臨時搜尋詞。若本地找不到可用圖片，Bot 優先用臨時搜尋詞查外部 API，沒有臨時詞時沿用已知標籤。外部結果會標示來源，查詢詞不會寫回標籤庫。

圖片入庫時，Gemini 優先理解圖片內的中、英、日文台詞與管理員補充說明，從通用詞庫選取一個情境及一到兩個情緒標籤，並產生 15–40 字的繁體中文台詞／場景摘要。匯入腳本的作品標籤另行附加，其分類記錄在 `data/work_tags.json`。

## 安裝與啟動

需要 Python 3.11 以上。在專案根目錄執行 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
.\.venv\Scripts\python.exe main.py
```

1. 到 [Discord Developer Portal](https://discord.com/developers/applications) 建立應用程式與 Bot，啟用 **Message Content Intent**，將 Bot Token 填入本機 `.env`。
2. 安裝到測試伺服器，使用 `bot` 與 `applications.commands` scopes。開啟 **View Channels**、**Send Messages**、**Embed Links** 和 **Read Message History**。MemeStack 圖片以附件傳送，需要 **Attach Files**。若在討論串使用，也需要 **Send Messages in Threads**。
3. 填寫聊天供應商的 API 金鑰。預設使用 Gemini，可設定 `LLM_PROVIDER=openai` 切換聊天分析。圖片分析及 MyGO 正式匯入仍使用 `GEMINI_API_KEY`。
4. 可用 `DISCORD_TEST_GUILD_ID` 將斜線指令同步到測試伺服器，留白則同步全域指令。測試頻道也需允許使用應用程式指令。

所有變數及模型預設值見 [.env.example](.env.example)。SQLite 預設位於 `data/memes.db`，相對路徑以專案根目錄為準。設定 `MEMESTACK_API_KEY` 時選用 MemeStack，否則可用 `WIKIMEDIA_CONTACT` 啟用 Commons。Commons 結果依授權條件篩選並附檔案頁面。圖片主機由 `IMAGE_HOSTS` 控制，MyGO 圖片需包含 `storageapi.miyago9267.com`。

## Discord 斜線指令

| 指令 | 權限與用途 |
| --- | --- |
| `/meme text` | 分析指定文字並推薦梗圖。有權限時可讀取近期對話供本地重排。 |
| `/meme_recent` | 分析頻道最近 12 則訊息中的人類文字並推薦梗圖。 |
| `/meme_help` | 顯示 Bot 使用說明。 |
| `/meme_add image_url tags [description]` | 管理伺服器。以直接 HTTPS 圖片網址與逗號分隔標籤新增本伺服器梗圖。 |
| `/meme_ai_add [image] [image_url] [description]` | 管理伺服器。附件與網址擇一，由 Gemini 分析並加入全域圖庫。未填描述時，自動儲存並顯示台詞／場景摘要。 |
| `/meme_tag_add meme_id tags` | 管理伺服器。為本伺服器或全域梗圖追加專有／作品標籤，支援逗號或空白分隔，去重後同步至已知詞庫。 |
| `/meme_edit meme_id tags` | 管理伺服器。依編號整組替換標籤，使用逗號分隔。 |
| `/meme_delete meme_id` | 管理伺服器。依編號刪除梗圖。 |

`/meme` 將指令文字交給設定的聊天供應商，額外讀到的近期訊息只用於本地重排。`/meme_recent` 則會把取出的近期聊天文字送給供應商。歷史讀取會略過 Bot 訊息、Webhook 和空白訊息。

## 批次匯入 MyGO

`scripts/import_mygo_api.py` 抓完 API 所有分頁再依人氣降序排序。正式匯入時會先查 URL 是否已存在，將出處、角色、情境與標題／台詞組成描述交給 Gemini，再把圖片寫入全域圖庫。結束時回報新增、跳過及失敗筆數。

| 參數 | 行為 |
| --- | --- |
| `--api-url URL` | HTTPS API 端點。省略時使用 `MYGO_API_URL`，兩者至少設定一項。 |
| `--top N` | 取符合門檻的人氣前 N 筆，預設 50，設為 0 時選取全部。 |
| `--min-popularity N` | 只選取人氣大於等於 N 的圖片，預設 0。 |
| `--extra-tag TAG` | 附加的作品標籤，預設 `mygo`，會同步登記至已知詞庫及作品分類。 |
| `--dry-run` | 預覽前 10 筆排序結果，無需 Gemini 金鑰，不寫入 SQLite 或詞庫。 |

```powershell
.\.venv\Scripts\python.exe scripts/import_mygo_api.py --api-url https://mygo.miyago9267.com/api/v1/images --top 20 --dry-run
.\.venv\Scripts\python.exe scripts/import_mygo_api.py --top 50 --min-popularity 500 --extra-tag mygo
```

第二個指令使用 `.env` 中的 `MYGO_API_URL`，並需要 `GEMINI_API_KEY`。

## 專案結構與測試

- `main.py` 與 `config.py` 負責啟動、設定和同步指令。
- `cogs/` 處理 Discord 指令。`services/` 提供分析、下載、SQLite、搜尋和回覆。`models/` 定義梗圖資料。
- `prompts/image_tagger.txt` 是圖片分析模板。`data/` 存放詞庫及本機 SQLite。
- `scripts/` 包含 MyGO、Imgflip、MemeStack 匯入工具，以及標籤清理與分析 CLI。
- `tests/` 包含分析、指令、匯入、搜尋及資料庫測試。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/test_extract_tags.py --provider gemini "這還有救嗎"
```

pytest 使用模擬外部服務與暫存 SQLite。第二個指令會實際呼叫指定模型並輸出 `{"tags": [...]}`，可能消耗 API 額度。
