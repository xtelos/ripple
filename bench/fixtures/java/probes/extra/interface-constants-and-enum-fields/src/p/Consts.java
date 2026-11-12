package p;

public class Consts {
    static class Item { void add(int x) {} }
    interface Holder {
        Item ITEM = new Item();
    }
    enum Slot {
        ONE(new Item());
        final Item item;
        Slot(Item i) { item = i; }
    }
    static class Impl implements Holder {
        void f() {
            ITEM.add(1);
            Holder.ITEM.add(2);
            Slot.ONE.item.add(3);
        }
    }
}
