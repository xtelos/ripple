#[cfg(unix)]
#[path = "unix.rs"]
mod sys_impl;
#[cfg(windows)]
#[path = "windows.rs"]
mod sys_impl;

pub fn sys() -> u32 {
    sys_impl::go()
}
