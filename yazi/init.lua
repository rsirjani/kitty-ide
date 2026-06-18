-- IDE: broadcast the explorer's current directory on every `cd` so the
-- claude terminal can follow it (see ~/.local/bin/ide-cwd-follow). The file is
-- keyed off THIS IDE's control socket (KITTY_LISTEN_ON = unix:/tmp/ide.sock-<pid>)
-- so sibling IDEs on other workspaces don't clobber each other's cwd.
local function ide_cwd_file()
	local s = os.getenv("KITTY_LISTEN_ON") or ""
	local id = s:match("ide%.sock%-(.+)$") or "default"
	return "/tmp/ide-" .. id .. "-explorer-cwd"
end

ps.sub("cd", function()
	local cwd = tostring(cx.active.current.cwd)
	local f = io.open(ide_cwd_file(), "w")
	if f then
		f:write(cwd)
		f:close()
	end
end)

-- IDE: double-click an item to activate it -- a directory is entered, a file is
-- opened via the openers (ide-open), the same as selecting it and pressing
-- Enter. yazi's stock left-click only *reveals* (hovers) the item; a second
-- left-click on the same item within DOUBLE_CLICK_SECS activates it. Single
-- left-clicks keep their normal reveal/hover behaviour; a right-click copies the
-- item's full path to the clipboard; middle is untouched.
local DOUBLE_CLICK_SECS = 0.4
local last_url, last_time = nil, 0

function Entity:click(event, up)
	if up or event.is_middle then
		return
	end

	ya.emit("reveal", { self._file.url }) -- hover the clicked item either way
	if event.is_right then
		-- right-click: copy the item's full path to the clipboard
		local path = tostring(self._file.url)
		ya.clipboard(path)
		ya.notify { title = "Copied path", content = path, timeout = 2 }
		return
	end

	-- left button: activate on a double-click (two clicks on the same item in
	-- time) -- enter a directory, or open a file via the openers, like Enter.
	local url = tostring(self._file.url)
	local now = ya.time()
	if url == last_url and now - last_time <= DOUBLE_CLICK_SECS then
		if self._file.cha.is_dir then
			ya.emit("enter", {}) -- reveal above set this dir as hovered
		else
			ya.emit("open", {})
		end
		last_url, last_time = nil, 0 -- consume, so a 3rd click starts a new pair
	else
		last_url, last_time = url, now
	end
end

-- IDE: double-click a segment of the path shown in the header to jump straight
-- to that directory level (e.g. click `src` in `~/src/kitty-ide` -> cd ~/src;
-- click the leading `/` or `~` -> cd to root / home). The level is found by
-- counting the `/` separators at or after the clicked column, so it is correct
-- even when the path is left-truncated to fit the narrow explorer.
local last_crumb, last_crumb_time = nil, 0

function Header:click(event, up)
	if up or not event.is_left then
		return
	end

	local cwd = tostring(self._current.cwd)
	local max = self._area.w - (self._right_width or 0)
	if max <= 0 then
		return
	end
	local ok, shown = pcall(function()
		return tostring(ui.truncate(ya.readable_path(cwd), { max = max, rtl = true }))
	end)
	if not ok then
		shown = ya.readable_path(cwd)
	end

	local col = event.x - self._area.x -- 0-based column within the path string
	if col < 0 then
		return
	end

	-- levels to go up = number of "/" separators at or after the clicked column
	local levels, c = 0, 0
	for _, code in utf8.codes(shown) do
		if utf8.char(code) == "/" and c >= col then
			levels = levels + 1
		end
		c = c + 1
	end
	if c == 0 or col >= c then
		return -- clicked past the end of the path text
	end

	-- build the target by dropping `levels` trailing components from the cwd
	local comps = {}
	for s in cwd:gmatch("[^/]+") do
		comps[#comps + 1] = s
	end
	for _ = 1, levels do
		table.remove(comps)
	end
	local target = "/" .. table.concat(comps, "/")

	-- double-click the same segment to jump
	local now = ya.time()
	if target == last_crumb and now - last_crumb_time <= DOUBLE_CLICK_SECS then
		ya.emit("cd", { target })
		last_crumb, last_crumb_time = nil, 0
	else
		last_crumb, last_crumb_time = target, now
	end
end
