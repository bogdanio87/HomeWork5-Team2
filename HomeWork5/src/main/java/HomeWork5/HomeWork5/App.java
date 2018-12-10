package HomeWork5.HomeWork5;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;

public class App {
	public static void main(String[] args) {
		ArrayList<String> students = new ArrayList<String>();
		try (BufferedReader task2 = new BufferedReader(
				new FileReader("src/main/java/HomeWork5/" + "HomeWork5/HWork5/students.txt"))) {
			String sCurrentLine;
			while ((sCurrentLine = task2.readLine()) != null) {
				students.add(sCurrentLine.trim());
			}
		} catch (IOException e) {
			System.out.println("File not exist ! ! !");
		}
		Collections.sort(students);
		System.out.println("ArrayList: " + students);
	}
}

