fn main() {
    println!("{}", mixed::add(1, 2));
}

#[cfg(test)]
mod tests {
    #[test]
    fn t_bin() {
        assert_eq!(1 + 2, 3);
    }
    #[test]
    fn same() {
        assert_eq!(1 + 1, 2);
    }
}
