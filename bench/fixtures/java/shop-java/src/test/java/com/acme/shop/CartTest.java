package com.acme.shop;

/** A plain main, so javac and java can run it without JUnit. */
class CartTest {
    static void addsItems() {
        Cart c = new Cart();
        c.add("a", 250);
        c.add(new Item("b", 100));
        if (c.total() != 350) throw new AssertionError(c.receipt());
        new BulkCart(2).fill();
        c.id();
    }

    public static void main(String[] args) throws Exception {
        addsItems();
        Ledger l = new Ledger();
        l.record(Item.free("z"));
        Reports.sum(java.util.List.of(new Cart(), new BulkCart(3)));
        System.out.println(Op.both(2) + " " + new Cart().copy().receipt());
    }
}
