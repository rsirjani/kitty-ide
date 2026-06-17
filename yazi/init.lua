-- IDE: broadcast the explorer's current directory on every `cd` so the
-- claude terminal can follow it (see ~/.local/bin/ide-cwd-follow).
ps.sub("cd", function()
	local cwd = tostring(cx.active.current.cwd)
	local f = io.open("/tmp/ide-explorer-cwd", "w")
	if f then
		f:write(cwd)
		f:close()
	end
end)
