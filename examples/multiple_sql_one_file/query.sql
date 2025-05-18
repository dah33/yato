CREATE TEMPORARY TABLE my_numbers (number INTEGER);

INSERT INTO my_numbers VALUES (1), (2), (3);

SELECT sum(number) FROM my_numbers;
