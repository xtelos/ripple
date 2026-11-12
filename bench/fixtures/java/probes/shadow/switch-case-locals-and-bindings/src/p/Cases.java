package p;

public class Cases {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    Money x = new Money();
    Money total = new Money();
    void old(int k) {
        switch (k) {
            case 1:
                Item x = new Item();
                x.add(1);
                break;
            case 2:
                x = new Item();
                x.add(2);
                break;
            default:
                this.x.add(3);
        }
        x.add(4);
    }
    void arrows(Object o, int k) {
        switch (k) {
            case 1 -> {
                Item x = new Item();
                x.add(5);
            }
            default -> x.add(6);
        }
        switch (o) {
            case Item total when total != null -> total.add(7);
            default -> total.add(8);
        }
        total.add(9);
    }
}
